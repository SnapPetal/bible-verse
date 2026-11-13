#!/usr/bin/env python3
"""One build, one private JAR, two app-only CloudFormation updates.

Local state is private scratch space, not an approval package or a resume protocol.
An interrupted mutation is never replayed: inspect AWS read-only before recovery.
The CLI needs the existing activated app-only roles and a trusted GitHub workflow.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import bible_deploy as d
import java_release as j
import source_promotion as promotion

REPO = 'SnapPetal/bible-verse'
WORKFLOW = '.github/workflows/java-release.yml'
CAPABILITIES = ['CAPABILITY_NAMED_IAM']
# Rollback-complete is stable, but still requires exact baseline, runtime and prior-operation checks.
STABLE_STACK_STATUSES = ('UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE')
DIAGNOSTIC = {'stage': 'source-check', 'operation': 'none'}
WRITE_OPERATIONS = {'put-object', 'create-change-set', 'execute-change-set'}

def failure_message(error):
    # Inspect only to select static labels; never emit exception text or AWS response bodies.
    reason = 'validation-failed'
    text = str(error) if isinstance(error, ValueError) else ''
    if isinstance(error, (subprocess.CalledProcessError, TimeoutError)):
        reason = 'uncertain-write' if DIAGNOSTIC['operation'] in WRITE_OPERATIONS else 'aws-read-failed'
    elif 'source/private baseline difference' in text or 'source infrastructure' in text:
        reason = 'source-scope-mismatch'
    elif 'not stable' in text or 'Stack changed' in text or 'Prior source operation' in text:
        reason = 'stack-or-prior-operation-requires-reconciliation'
    elif 'not Active' in text or 'Latest function not ready' in text:
        reason = 'version-not-ready'
    elif 'CloudFormation' in text or 'change set' in text.lower():
        reason = 'cloudformation-validation-or-state-failed'
    return ('Deployment stopped: stage=' + DIAGNOSTIC['stage'] + ' operation=' + DIAGNOSTIC['operation']
            + ' reason=' + reason + '. Inspect Bible stack events/change sets in CloudFormation; '
            'do not blindly rerun an uncertain write.')

CONFIG_KEYS = ('Runtime', 'Handler', 'Architectures', 'MemorySize', 'Timeout',
               'Environment', 'Layers', 'KMSKeyArn', 'VpcConfig', 'DeadLetterConfig',
               'TracingConfig', 'EphemeralStorage', 'FileSystemConfigs', 'LoggingConfig', 'Role')


def digest(value):
    return d.sha(d.canonical(value))


def operation_name(source, phase):
    """Independent of run ID, mutable baseline and artifact version: reruns collide."""
    d.need(re.fullmatch('[a-f0-9]{40}', source) and phase in (1, 2), 'Invalid operation identity')
    return 'bible-isolated-update-java-' + digest([d.STACK, source, phase])[:32]


def jar_asset(data):
    d.need(bool(data), 'Empty JAR')
    return {'bucket': d.BUCKET, 'owner': d.ACCOUNT, 'encryption': 'AES256',
            'key': 'assets/' + d.sha(data) + '.jar', 'bytes': len(data),
            'sha256_base64': base64.b64encode(hashlib.sha256(data).digest()).decode(),
            'checksum_type': 'FULL_OBJECT'}


def config_digest(config):
    d.need(all(key in config for key in ('Runtime', 'Handler', 'MemorySize', 'Timeout', 'Role')),
           'Incomplete function configuration')
    d.need('Error' not in config.get('Environment', {}), 'Cannot verify function environment')
    d.need(config.get('SnapStart', {}).get('ApplyOn') == 'PublishedVersions', 'SnapStart configuration drift')
    # Never persist actual environment values or raw GetFunctionConfiguration responses.
    return digest({key: config.get(key) for key in CONFIG_KEYS})


def check_version(config, fid, number, code_hash, expected_config=None):
    arn = f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{j.FUNCTIONS[fid]["name"]}'
    d.need(re.fullmatch('[1-9][0-9]*', number), 'Unqualified Lambda version')
    d.need(config.get('FunctionArn') == arn + ':' + number and config.get('Version') == number,
           'Wrong function version')
    d.need(config.get('CodeSha256') == code_hash and config.get('Role') == j.RUNTIME_ROLES[fid][1],
           'Version code or role differs')
    d.need(config.get('State') == 'Active' and config.get('SnapStart', {}).get('OptimizationStatus') == 'On',
           'Version not Active with SnapStart On')
    result = config_digest(config)
    d.need(expected_config is None or result == expected_config, 'Published configuration drift')
    return result


def derive(source, live, asset, old_versions, source_commit):
    """Pure bounded transformation; no historical synth, review hashes or approval inputs."""
    d.need(not source.get('Transform') and not live.get('Transform'), 'Transforms unsupported')
    promotion.bridge(source, live)
    d.need(set(old_versions) == set(j.FUNCTIONS), 'Incomplete runtime observations')
    versions = {}
    noop = []
    for fid, info in j.FUNCTIONS.items():
        old_id = j.version_ref(live['Resources'][info['alias']])
        new_id = j.version_ref(source['Resources'][info['alias']])
        old = old_versions[fid]
        arn = f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{info["name"]}'
        d.need(old.get('logical_id') == old_id and re.fullmatch('[1-9][0-9]*', old.get('version', '')),
               'Old version observation differs from template')
        d.need(old.get('physical_id') == arn + ':' + old['version'], 'Old version physical mapping differs')
        d.need(live['Resources'][fid]['Properties'].get('Role') ==
               {'Fn::GetAtt': [j.RUNTIME_ROLES[fid][0], 'Arn']}, 'Unexpected runtime role reference')
        noop.append(old['code_sha256'] == asset['sha256_base64'])
        versions[fid] = {'old_logical': old_id, 'new_logical': new_id}
    d.need(all(noop) or not any(noop), 'Mixed app code baseline requires review')
    result = {'source_commit': source_commit, 'baseline': live, 'source': source,
              'asset': asset, 'old_versions': old_versions, 'versions': versions, 'noop': all(noop)}
    if result['noop']:
        return result
    phase1 = copy.deepcopy(live)
    phase2 = copy.deepcopy(live)
    code = {'S3Bucket': d.BUCKET, 'S3Key': asset['key'], 'S3ObjectVersion': asset['version_id']}
    j.artifact(asset, code)
    for fid, info in j.FUNCTIONS.items():
        old_id, new_id = versions[fid]['old_logical'], versions[fid]['new_logical']
        d.need(new_id != old_id and new_id not in live['Resources'], 'New code requires fresh source version IDs')
        old = live['Resources'][old_id]
        d.need(old.get('DeletionPolicy') == old.get('UpdateReplacePolicy') == 'Retain', 'Old version must be retained')
        new = copy.deepcopy(old)
        new['Properties'] = {'FunctionName': {'Ref': fid}, 'CodeSha256': asset['sha256_base64']}
        for template in (phase1, phase2):
            template['Resources'][fid]['Properties']['Code'] = copy.deepcopy(code)
            template['Resources'][new_id] = copy.deepcopy(new)
        phase2['Resources'].pop(old_id)
        phase2['Resources'][info['alias']]['Properties']['FunctionVersion'] = {'Fn::GetAtt': [new_id, 'Version']}
    for template in (phase1, phase2):
        d.need(len(d.canonical(template)) <= 51200, 'Template exceeds inline CFN limit; explicit private template support needed')
    result.update(phase1=phase1, phase2=phase2)
    return result


def phase1_plan(source, live, asset, old_versions, source_commit):
    """Recognize only the exact completed first phase of this built application."""
    projection = copy.deepcopy(live)
    expected = set()
    for fid, info in j.FUNCTIONS.items():
        old_id = j.version_ref(live['Resources'][info['alias']])
        new_id = j.version_ref(source['Resources'][info['alias']])
        d.need(old_id != new_id and new_id in live['Resources'], 'Completed phase one versions missing')
        expected.update((old_id, new_id))
        projection['Resources'].pop(new_id)
        code = live['Resources'][fid]['Properties']['Code']
        d.need(code == {'S3Bucket': d.BUCKET, 'S3Key': asset['key'], 'S3ObjectVersion': asset['version_id']},
               'Completed phase one artifact differs')
    d.need({k for k, v in live['Resources'].items() if v['Type'] == 'AWS::Lambda::Version'} == expected,
           'Unexpected managed versions in completed phase one')
    # This projection is comparison-only. It is never submitted as a template.
    plan = derive(source, projection, asset, old_versions, source_commit)
    d.need(not plan['noop'] and plan['phase1'] == live, 'Live template is not exact completed phase one')
    plan['start_phase'] = 2
    return plan


def validate_changes(plan, phase, changeset, template, created):
    """Only this process's successful CreateChangeSet response can lead to execution."""
    name = operation_name(plan['source_commit'], phase)
    arn = changeset.get('ChangeSetId', '')
    d.need(re.fullmatch(r'arn:aws:cloudformation:us-east-1:664759038511:changeSet/' + name + r'/[A-Za-z0-9-]+', arn),
           'Unexpected change set identity')
    d.need(created.get('Id') == arn and created.get('StackId') == changeset.get('StackId') == d.STACK,
           'Successful own create response unavailable')
    d.need(changeset.get('ChangeSetName') == name and changeset.get('Description') == f'simple-java:{plan["source_commit"]}:phase{phase}',
           'Change set request differs')
    d.need(changeset.get('Capabilities') == CAPABILITIES, 'Unexpected capabilities')
    d.need(changeset.get('Status') == 'CREATE_COMPLETE' and changeset.get('ExecutionStatus') == 'AVAILABLE',
           'Change set is not executable')
    d.need(not any(changeset.get(key) for key in ('NextToken', 'IncludeNestedStacks', 'RootChangeSetId', 'ParentChangeSetId')),
           'Nested or incomplete change set')
    d.need(template == plan[f'phase{phase}'], 'Change set template differs')
    expected = ({fid: ('Modify', 'AWS::Lambda::Function') for fid in j.FUNCTIONS} if phase == 1 else
                {info['alias']: ('Modify', 'AWS::Lambda::Alias') for info in j.FUNCTIONS.values()})
    expected.update({v['new_logical' if phase == 1 else 'old_logical']:
                     ('Add' if phase == 1 else 'Remove', 'AWS::Lambda::Version') for v in plan['versions'].values()})
    seen = set()
    for item in changeset.get('Changes', []):
        change = item['ResourceChange']
        logical = change['LogicalResourceId']
        d.need(logical in expected and logical not in seen, 'Unexpected or duplicate resource delta')
        seen.add(logical)
        d.need((change['Action'], change['ResourceType']) == expected[logical], 'Unexpected resource action')
        d.need(change.get('Replacement') == 'False' if change['Action'] == 'Modify' else
               change.get('Replacement', 'False') == 'False', 'Replacement or unresolved action forbidden')
        if change['Action'] == 'Remove':
            d.need(change.get('PolicyAction') == 'Retain', 'Old physical version would not be retained')
        if change['Action'] == 'Modify':
            d.need(set(change.get('Scope', [])) == {'Properties'}, 'Non-property or missing modification scope')
            # Enhanced contexts stay in memory. Missing/unresolved views never grant permission.
            contexts = []
            for key in ('BeforeContext', 'AfterContext'):
                raw = change.get(key)
                d.need(isinstance(raw, str), 'Enhanced property context unavailable')
                context = json.loads(raw)
                d.need(isinstance(context, dict) and isinstance(context.get('Properties'), dict),
                       'Malformed enhanced property context')
                contexts.append(context)
            before, after = contexts
            property_name = 'Code' if phase == 1 else 'FunctionVersion'
            before_value = before['Properties'].pop(property_name, None)
            after_value = after['Properties'].pop(property_name, None)
            d.need(before_value is not None and after_value is not None and before == after,
                   'Resolved configuration changed outside app code or alias version')
            if phase == 1:
                d.need(before_value == plan['baseline']['Resources'][logical]['Properties']['Code'],
                       'Resolved code baseline differs')
                d.need(after_value == plan['phase1']['Resources'][logical]['Properties']['Code'],
                       'Resolved code does not match private JAR version')
            else:
                fid = next(fid for fid, info in j.FUNCTIONS.items() if info['alias'] == logical)
                d.need(str(before_value) == plan['old_versions'][fid]['version'], 'Resolved alias baseline differs')
                d.need(isinstance(after_value, (str, int)) and re.fullmatch('[1-9][0-9]*', str(after_value)),
                       'Unresolved alias version')
                d.need(str(after_value) == plan['ready_versions'][fid], 'Resolved alias target is not the ready version')
    d.need(seen == set(expected), 'Incomplete app change set')


class Runner:
    def __init__(self, aws=d.aws, sleep=time.sleep):
        def tracked(service, operation, **kwargs):
            DIAGNOSTIC['operation'] = operation
            return aws(service, operation, **kwargs)
        self.aws, self.sleep = tracked, sleep

    def actor(self, role):
        identity = self.aws('sts', 'get-caller-identity')
        d.need(identity.get('Account') == d.ACCOUNT and
               identity.get('Arn', '').startswith(f'arn:aws:sts::{d.ACCOUNT}:assumed-role/{role}/'), 'Wrong AWS role')

    def stack(self):
        stacks = self.aws('cloudformation', 'describe-stacks', stack_name=d.STACK)['Stacks']
        d.need(len(stacks) == 1 and stacks[0].get('StackId') == d.STACK and
               stacks[0].get('RoleARN') == d.EXECUTOR, 'Wrong stack or executor')
        return stacks[0]

    def template(self, changeset=None):
        args = {'stack_name': d.STACK, 'template_stage': 'Original'}
        if changeset:
            args['change_set_name'] = changeset
        return d.original(self.aws('cloudformation', 'get-template', **args))

    def describe(self, name):
        try:
            result = self.aws('cloudformation', 'describe-change-set', stack_name=d.STACK,
                              change_set_name=name, include_property_values=True)
        except subprocess.CalledProcessError as error:
            message = error.stderr or ''
            if '(ChangeSetNotFound)' in message or ('(ValidationError)' in message and 'ChangeSet' in message and 'does not exist' in message):
                return None
            raise
        d.need(not result.get('NextToken'), 'Incomplete change set observation')
        return result

    def fresh(self, source):
        stack = self.stack()
        d.need(stack['StackStatus'] in STABLE_STACK_STATUSES, 'Stack is not stable; inspect before retrying')
        prior = stack.get('ChangeSetId', '')
        if prior.startswith(f'arn:aws:cloudformation:{d.REGION}:{d.ACCOUNT}:changeSet/bible-isolated-update-java-'):
            observed = self.describe(prior)
            d.need(observed is not None and observed.get('StackId') == d.STACK and
                   observed.get('Status') == 'CREATE_COMPLETE' and
                   observed.get('ExecutionStatus') in ('EXECUTE_COMPLETE', 'AVAILABLE'),
                   'Prior stack operation is not reconciled')
        for phase in (1, 2):
            d.need(self.describe(operation_name(source, phase)) is None,
                   'Prior source operation exists; read-only reconciliation required, never rerun')

    def controls(self, activation):
        args = {'bucket': d.BUCKET, 'expected_bucket_owner': d.ACCOUNT}
        policy = json.loads(self.aws('s3api', 'get-bucket-policy', **args)['Policy'])
        d.need(digest(policy) == activation['asset_policy_sha256'], 'Private bucket policy differs')
        d.need(self.aws('s3api', 'get-bucket-versioning', **args).get('Status') == 'Enabled', 'Bucket versioning disabled')
        block = self.aws('s3api', 'get-public-access-block', **args)['PublicAccessBlockConfiguration']
        d.need(all(block.get(key) is True for key in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')),
               'Private bucket public access block differs')
        try:
            d.need(not self.aws('s3api', 'get-bucket-lifecycle-configuration', **args).get('Rules'), 'Unexpected asset lifecycle')
        except subprocess.CalledProcessError as error:
            d.need('(NoSuchLifecycleConfiguration)' in (error.stderr or ''), 'Cannot verify lifecycle absence')

    def mapping(self, fid, logical):
        mapping = self.aws('cloudformation', 'describe-stack-resource', stack_name=d.STACK,
                           logical_resource_id=logical)['StackResourceDetail']
        d.need(mapping.get('StackId') == d.STACK and mapping.get('LogicalResourceId') == logical and
               mapping.get('ResourceType') == 'AWS::Lambda::Version', 'Wrong CFN version mapping')
        physical = mapping['PhysicalResourceId']
        number = physical.rsplit(':', 1)[-1]
        arn = f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{j.FUNCTIONS[fid]["name"]}'
        d.need(re.fullmatch('[1-9][0-9]*', number) and physical == arn + ':' + number, 'Wrong physical version mapping')
        return physical, number

    def observe(self, template, old_versions=None, versions=None, aliases_new=False):
        d.need(self.stack()['StackStatus'] in STABLE_STACK_STATUSES and self.template() == template, 'Stack changed or is incomplete')
        observations = {}
        for fid, info in j.FUNCTIONS.items():
            alias = self.aws('lambda', 'get-alias', function_name=info['name'], name='live')
            arn = f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{info["name"]}'
            d.need(alias.get('AliasArn') == arn + ':live' and
                   not alias.get('RoutingConfig', {}).get('AdditionalVersionWeights'), 'Wrong or weighted live alias')
            if old_versions is None:
                logical = j.version_ref(template['Resources'][info['alias']])
                physical, number = self.mapping(fid, logical)
                old = {'logical_id': logical, 'physical_id': physical, 'version': number}
            else:
                old = old_versions[fid]
                if old['logical_id'] in template['Resources']:
                    d.need(self.mapping(fid, old['logical_id']) == (old['physical_id'], old['version']), 'Old mapping changed')
            config = self.aws('lambda', 'get-function-configuration', function_name=info['name'], qualifier=old['version'])
            code = config['CodeSha256'] if old_versions is None else old['code_sha256']
            fingerprint = check_version(config, fid, old['version'], code, old.get('configuration_sha256'))
            old = {**old, 'code_sha256': code, 'configuration_sha256': fingerprint}
            current = old
            if versions is not None:
                physical, number = self.mapping(fid, versions[fid]['new_logical'])
                d.need(int(number) > int(old['version']), 'Published version is not new')
                new = self.aws('lambda', 'get-function-configuration', function_name=info['name'], qualifier=number)
                code = template['Resources'][versions[fid]['new_logical']]['Properties']['CodeSha256']
                check_version(new, fid, number, code, fingerprint)
                current = {'physical_id': physical, 'version': number, 'code_sha256': code}
            d.need(alias.get('FunctionVersion') == (current['version'] if aliases_new else old['version']), 'Live alias changed unexpectedly')
            latest = self.aws('lambda', 'get-function-configuration', function_name=info['name'])
            d.need(latest.get('FunctionArn') == arn and latest.get('Version') == '$LATEST' and
                   latest.get('State') == 'Active' and latest.get('LastUpdateStatus') == 'Successful', 'Latest function not ready')
            d.need(latest.get('CodeSha256') == current['code_sha256'] and config_digest(latest) == fingerprint,
                   'Latest code or configuration drift')
            observations[fid] = {**old, **({'new_version': current['version']} if versions is not None else {})}
        return observations

    def head(self, asset, version=None):
        args = {'bucket': d.BUCKET, 'key': asset['key'], 'expected_bucket_owner': d.ACCOUNT, 'checksum_mode': 'ENABLED'}
        if version:
            args['version_id'] = version
        return self.aws('s3api', 'head-object', **args)

    def verify_asset(self, asset):
        result = d.object_receipt(self.head(asset, asset['version_id']), asset)
        d.need(result['version_id'] == asset['version_id'], 'Wrong immutable object version')

    def publish(self, data, state):
        asset = state['plan']['asset']
        d.need(jar_asset(data) == {k: v for k, v in asset.items() if k != 'version_id'}, 'Built JAR changed')
        d.exclusive_intent(state['directory'] / 'publish-intent.json', {'key': asset['key']})
        with tempfile.TemporaryDirectory(dir=state['directory']) as temporary:
            frozen = Path(temporary) / 'app.jar'
            frozen.write_bytes(data)
            try:
                response = self.aws('s3api', 'put-object', bucket=d.BUCKET, key=asset['key'], body=str(frozen),
                                    expected_bucket_owner=d.ACCOUNT, server_side_encryption='AES256', storage_class='STANDARD',
                                    checksum_algorithm='SHA256', checksum_sha256=asset['sha256_base64'], if_none_match='*')
            except subprocess.CalledProcessError as error:
                # A 412 is a definite non-write; validate/reuse the existing content address.
                # No preflight HEAD of missing keys: that needs absent ListBucket permission.
                d.need('(PreconditionFailed)' in (error.stderr or '') or '(412)' in (error.stderr or ''),
                       'Upload result uncertain; inspect without retry')
                receipt = d.object_receipt(self.head(asset), asset)
                return {**asset, 'version_id': receipt['version_id']}
        d.need(response.get('VersionId') not in (None, '', 'null'), 'Upload result uncertain; inspect without retry')
        asset = {**asset, 'version_id': response['VersionId']}
        self.verify_asset(asset)
        return asset

    def validation_gate(self, changeset):
        # Reuse the existing bounded read-only validation event check, not its approval protocol.
        import java_adapter
        return java_adapter.Adapter.validation_gate(self, changeset)

    @property
    def t(self):
        return self

    def wait_change(self, name, execute=False):
        for _ in range(120):
            result = self.describe(name)
            d.need(result is not None, 'Submitted change set absent; never resubmit')
            value = result.get('ExecutionStatus') if execute else result.get('Status')
            if value == ('EXECUTE_COMPLETE' if execute else 'CREATE_COMPLETE'):
                return result
            d.need(value in (('AVAILABLE', 'EXECUTE_IN_PROGRESS') if execute else ('CREATE_PENDING', 'CREATE_IN_PROGRESS')),
                   'CloudFormation operation failed; inspect privately, never rerun')
            self.sleep(5)
        raise ValueError('CloudFormation still pending after ten minutes; inspect without retry')

    def wait_stack(self, expected_template, before):
        # ExecuteChangeSet starts an asynchronous stack update. Its change set may
        # no longer be retrievable; DescribeStacks is the execution status source.
        # Tolerate an unchanged pre-execution snapshot, never accept it as success.
        identity = lambda value: tuple(value.get(k) for k in ('StackStatus', 'LastUpdatedTime', 'ChangeSetId'))
        progressed = False
        for _ in range(120):
            stack = self.stack()  # Pins the exact stack ARN and execution role.
            status = stack['StackStatus']
            if status == 'UPDATE_COMPLETE' and self.template() == expected_template:
                return stack
            unchanged = identity(stack) == identity(before)
            d.need((not progressed and unchanged) or status in
                   ('UPDATE_IN_PROGRESS', 'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'),
                   'CloudFormation stack update failed or template differs; inspect without retry')
            progressed = progressed or not unchanged
            self.sleep(5)
        raise ValueError('CloudFormation stack still pending after ten minutes; inspect without retry')

    def deploy(self, state, activation, source_check=lambda: None):
        plan, directory = state['plan'], state['directory']
        self.fresh(plan['source_commit'])
        self.controls(activation)
        self.verify_asset(plan['asset'])
        ready = None
        if plan.get('start_phase') == 2:
            ready = self.observe(plan['phase1'], plan['old_versions'], plan['versions'])
            plan['ready_versions'] = {fid: value['new_version'] for fid, value in ready.items()}
        for phase in ((2,) if plan.get('start_phase') == 2 else (1, 2)):
            before = 'baseline' if phase == 1 else 'phase1'
            expected = self.observe(plan[before], plan['old_versions'], None if phase == 1 else plan['versions'])
            source_check()
            self.controls(activation)
            self.verify_asset(plan['asset'])
            name = operation_name(plan['source_commit'], phase)
            d.need(self.describe(name) is None, 'Prior operation exists; no automatic recovery')
            d.exclusive_intent(directory / f'phase{phase}-create-intent.json', {'name': name})
            created = self.aws('cloudformation', 'create-change-set', stack_name=d.STACK, change_set_name=name,
                               change_set_type='UPDATE', role_arn=d.EXECUTOR, capabilities=CAPABILITIES,
                               template_body=d.canonical(plan[f'phase{phase}']).decode(),
                               client_token=digest([name, 'create']),
                               description=f'simple-java:{plan["source_commit"]}:phase{phase}')
            changeset = self.wait_change(name)
            validate_changes(plan, phase, changeset, self.template(name), created)
            self.validation_gate(changeset)
            # Re-read everything that matters immediately before the single execute attempt.
            source_check()
            self.controls(activation)
            self.verify_asset(plan['asset'])
            d.need(self.observe(plan[before], plan['old_versions'], None if phase == 1 else plan['versions']) == expected,
                   'Runtime changed after preparation')
            changeset = self.describe(name)
            validate_changes(plan, phase, changeset, self.template(name), created)
            self.validation_gate(changeset)
            d.exclusive_intent(directory / f'phase{phase}-execute-intent.json', {'name': name, 'id': created['Id']})
            before_execute = self.stack()
            self.aws('cloudformation', 'execute-change-set', stack_name=d.STACK, change_set_name=created['Id'],
                     client_request_token=digest([name, 'execute']))
            self.wait_stack(plan[f'phase{phase}'], before_execute)
            # Phase one must be fully Active/On before the alias change can even be created.
            result = self.observe(plan[f'phase{phase}'], plan['old_versions'], plan['versions'], aliases_new=phase == 2)
            if phase == 1:
                ready = result
                plan['ready_versions'] = {fid: value['new_version'] for fid, value in result.items()}
            else:
                d.need(result == ready, 'Previously verified version mapping changed')
        d.save(directory / 'complete.json', {'source_commit': plan['source_commit'], 'complete': True})


def context():
    activation = d.java_active()
    import select_application
    select_application.select()  # Recheck pinned application ancestry and exact allowed diff.
    source = os.environ.get('GITHUB_SHA', '')
    d.need(os.environ.get('GITHUB_REPOSITORY') == REPO and os.environ.get('GITHUB_REF') == 'refs/heads/main' and
           re.fullmatch('[a-f0-9]{40}', source), 'Wrong repository, branch or source')
    checked_out = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=d.ROOT, text=True).strip()
    d.need(checked_out == source, 'Checked-out source differs from authenticated event')
    # A newer queued push must not interrupt this already pinned two-stage operation.
    d.need(os.environ.get('GITHUB_WORKFLOW_REF') == REPO + '/' + WORKFLOW + '@refs/heads/main', 'Wrong workflow')
    d.need(os.environ.get('GITHUB_EVENT_NAME') in ('push', 'workflow_dispatch') and
           re.fullmatch('[1-9][0-9]*', os.environ.get('GITHUB_RUN_ATTEMPT', '')),
           'A valid trusted push or dispatch attempt is required')
    event = d.load(os.environ['GITHUB_EVENT_PATH'])
    d.need(event.get('repository', {}).get('full_name') == REPO, 'Wrong event repository')
    actor = int(os.environ.get('GITHUB_ACTOR_ID', '0'))
    d.need(actor > 0 and event.get('sender', {}).get('id') == actor, 'Event actor differs')
    if os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch':
        d.need(actor in activation['java_release_actor_ids'], 'Dispatch actor not allowed')
    else:
        d.need(event.get('ref') == 'refs/heads/main' and event.get('after') == source and not event.get('deleted'), 'Wrong push source')
    return activation, source


def check_staged_jar(source, template_path, data):
    for fid in j.FUNCTIONS:
        code = source['Resources'][fid]['Properties']['Code']
        d.need(set(code) == {'S3Bucket', 'S3Key'} and re.fullmatch('[a-f0-9]{64}\\.jar', code.get('S3Key', '')),
               'Unexpected synthesized app asset')
        staged = template_path.parent / ('asset.' + code['S3Key'])
        d.need(staged.is_file() and staged.read_bytes() == data, 'Synthesized asset does not match built JAR')


def output_noop(noop):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('noop=' + str(noop).lower() + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'publish', 'deploy'))
    parser.add_argument('--state', type=Path, default=Path('.private/simple-deploy'))
    parser.add_argument('--template', type=Path, default=Path('.private/cdk-source/bible-verse-stack.template.json'))
    parser.add_argument('--jar', type=Path, default=Path('target/bibleverse-2.0.0-aws.jar'))
    args = parser.parse_args()
    DIAGNOSTIC.update(stage=args.mode, operation='none')
    activation, source_commit = context()
    runner = Runner()
    directory = args.state
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / 'plan.json'
    if args.mode == 'plan':
        d.need(not path.exists(), 'Existing local operation; inspect without rerunning')
        runner.actor('BibleVerseCdkDeploy')
        d.need(runner.stack()['StackStatus'] in STABLE_STACK_STATUSES, 'Stack is not stable')
        runner.controls(activation)
        source, data = d.load(args.template), args.jar.read_bytes()
        check_staged_jar(source, args.template, data)
        live = runner.template()
        import select_application
        _, application_commit = select_application.select()
        if application_commit != source_commit:
            # A manual retry may finish phase two only when the original build is identical.
            versions = {fid: {'new_logical': j.version_ref(source['Resources'][info['alias']])}
                        for fid, info in j.FUNCTIONS.items()}
            old = runner.observe(live, versions=versions)
            code_refs = [live['Resources'][fid]['Properties']['Code'] for fid in j.FUNCTIONS]
            d.need(all(code == code_refs[0] for code in code_refs), 'Mixed phase one artifacts')
            asset = {**jar_asset(data), 'version_id': code_refs[0].get('S3ObjectVersion')}
            runner.verify_asset(asset)
            plan = phase1_plan(source, live, asset, old, source_commit)
        else:
            old = runner.observe(live)
            # Validate scope and inline size before publishing. This marker is never submitted.
            asset = {**jar_asset(data), 'version_id': 'LOCAL-UNPUBLISHED'}
            plan = derive(source, live, asset, old, source_commit)
        plan['application_commit'] = application_commit
        if plan['noop']:
            for phase in (1, 2):
                runner.describe(operation_name(source_commit, phase))
        else:
            runner.fresh(source_commit)
        d.exclusive_intent(path, {'run_id': os.environ['GITHUB_RUN_ID'], 'plan': plan})
    else:
        state = d.load(path)
        d.need(state['run_id'] == os.environ['GITHUB_RUN_ID'] and state['plan']['source_commit'] == source_commit,
               'Local state is not from this run and source')
        plan = state['plan']
    output_noop(plan['noop'])
    if plan['noop'] or args.mode == 'plan':
        return
    state = {'directory': directory, 'plan': plan}
    if args.mode == 'publish':
        runner.actor('BibleVerseCdkFilePublisher')
        if plan.get('start_phase') == 2:
            d.need(jar_asset(args.jar.read_bytes()) == {k: v for k, v in plan['asset'].items() if k != 'version_id'},
                   'Built JAR changed')
            runner.verify_asset(plan['asset'])
            return  # Existing immutable phase-one object; never publish or rebuild versions.
        asset = runner.publish(args.jar.read_bytes(), state)
        plan = derive(plan['source'], plan['baseline'], asset, plan['old_versions'], source_commit)
        d.save(path, {'run_id': os.environ['GITHUB_RUN_ID'], 'plan': plan})
    else:
        runner.actor('BibleVerseCdkDeploy')
        d.need(plan['asset']['version_id'] != 'LOCAL-UNPUBLISHED', 'Private JAR has not been published')
        runner.deploy(state, activation, source_check=context)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Static stage/operation/reason only; no raw AWS or private configuration.
        raise SystemExit(failure_message(error)) from None


