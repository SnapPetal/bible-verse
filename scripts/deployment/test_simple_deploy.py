"""Focused offline contract tests. Every AWS operation below is a fake."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import simple_deploy as s
import bible_deploy as d
import java_release as j

SOURCE = 'a' * 40
DATA = b'compiled app fixture'
FIXTURE = Path(__file__).parent / 'fixtures/java-release/baseline.json'


def fixture():
    live = json.loads(FIXTURE.read_text())
    source = copy.deepcopy(live)
    old = {}
    for i, (fid, info) in enumerate(j.FUNCTIONS.items()):
        logical = j.version_ref(live['Resources'][info['alias']])
        number = str(101 + i * 106)
        old[fid] = {'logical_id': logical, 'physical_id': f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{info["name"]}:{number}',
                    'version': number, 'code_sha256': base64.b64encode(b'o' * 32).decode()}
        new = logical + 'Next'
        source['Resources'][new] = source['Resources'].pop(logical)
        source['Resources'][info['alias']]['Properties']['FunctionVersion'] = {'Fn::GetAtt': [new, 'Version']}
        source['Resources'][fid]['Properties']['Code']['S3Key'] = 'e' * 64 + '.jar'
    live['Resources']['CDKMetadata'] = {'Type': 'AWS::CDK::Metadata', 'Properties': {'Analytics': 'preserved'}}
    asset = {**s.jar_asset(DATA), 'version_id': 'private-object-version'}
    return source, live, asset, old


def plan():
    return s.derive(*fixture(), SOURCE)


def config(fid, old, number=None, code=None):
    number = number or old['version']
    arn = f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{j.FUNCTIONS[fid]["name"]}'
    return {'FunctionArn': arn + (':' + number if number != '$LATEST' else ''), 'Version': number,
            'CodeSha256': code or old['code_sha256'], 'Role': j.RUNTIME_ROLES[fid][1],
            'Runtime': 'java25', 'Handler': 'handler', 'MemorySize': 512, 'Timeout': 10,
            'Environment': {'Variables': {'PRIVATE_TEST_VALUE': 'must-not-be-persisted'}},
            'State': 'Active', 'LastUpdateStatus': 'Successful',
            'SnapStart': {'ApplyOn': 'PublishedVersions', 'OptimizationStatus': 'On'}}


def changes(p, phase):
    name = s.operation_name(SOURCE, phase)
    before = p['baseline' if phase == 1 else 'phase1']['Resources']
    after = p[f'phase{phase}']['Resources']
    items = []
    for fid, info in j.FUNCTIONS.items():
        logical = fid if phase == 1 else info['alias']
        b, a = copy.deepcopy(before[logical]), copy.deepcopy(after[logical])
        if phase == 2:
            b['Properties']['FunctionVersion'] = p['old_versions'][fid]['version']
            a['Properties']['FunctionVersion'] = p['ready_versions'][fid]
        items.append({'ResourceChange': {'LogicalResourceId': logical, 'Action': 'Modify',
                      'ResourceType': b['Type'], 'Replacement': 'False', 'Scope': ['Properties'],
                      'BeforeContext': json.dumps(b), 'AfterContext': json.dumps(a)}})
        version_id = p['versions'][fid]['new_logical' if phase == 1 else 'old_logical']
        items.append({'ResourceChange': {'LogicalResourceId': version_id,
                      'Action': 'Add' if phase == 1 else 'Remove', 'ResourceType': 'AWS::Lambda::Version',
                      **({'PolicyAction': 'Retain'} if phase == 2 else {})}})
    arn = f'arn:aws:cloudformation:{d.REGION}:{d.ACCOUNT}:changeSet/{name}/fixture-id'
    return {'ChangeSetName': name, 'ChangeSetId': arn, 'StackId': d.STACK,
            'Description': f'simple-java:{SOURCE}:phase{phase}', 'Capabilities': s.CAPABILITIES,
            'Status': 'CREATE_COMPLETE', 'ExecutionStatus': 'AVAILABLE', 'Changes': items}


class Cloud:
    def __init__(self, p):
        self.p, self.current, self.phase = p, copy.deepcopy(p['baseline']), 0
        self.calls, self.sets, self.templates = [], {}, {}
        self.fail_create = self.fail_execute = self.fail_upload = None
        self.not_ready = False

    def aws(self, service, operation, **kw):
        self.calls.append((service, operation, kw))
        if operation == 'describe-stacks':
            return {'Stacks': [{'StackId': d.STACK, 'RoleARN': d.EXECUTOR, 'StackStatus': 'UPDATE_COMPLETE'}]}
        if operation == 'get-template':
            return {'TemplateBody': self.templates[kw['change_set_name']] if 'change_set_name' in kw else self.current}
        if operation == 'describe-change-set':
            name = kw['change_set_name']
            result = self.sets.get(name) or next((v for v in self.sets.values() if v['ChangeSetId'] == name), None)
            if result is None:
                raise subprocess.CalledProcessError(255, 'aws', stderr='(ChangeSetNotFound)')
            return copy.deepcopy(result)
        if operation == 'create-change-set':
            phase = 1 if kw['change_set_name'] == s.operation_name(SOURCE, 1) else 2
            self.sets[kw['change_set_name']] = changes(self.p, phase)
            self.templates[kw['change_set_name']] = json.loads(kw['template_body'])
            if self.fail_create:
                raise self.fail_create
            return {'Id': self.sets[kw['change_set_name']]['ChangeSetId'], 'StackId': d.STACK}
        if operation == 'execute-change-set':
            cs = next(v for v in self.sets.values() if v['ChangeSetId'] == kw['change_set_name'])
            if self.fail_execute:
                raise self.fail_execute
            self.phase += 1
            self.current = copy.deepcopy(self.p[f'phase{self.phase}'])
            cs['ExecutionStatus'] = 'EXECUTE_COMPLETE'
            return {}
        if operation == 'describe-events':
            return {'OperationEvents': []}
        if operation == 'describe-stack-resource':
            logical = kw['logical_resource_id']
            fid = next(fid for fid, v in self.p['versions'].items() if logical in v.values())
            old = self.p['old_versions'][fid]
            number = old['version'] if logical == old['logical_id'] else str(int(old['version']) + 1)
            return {'StackResourceDetail': {'StackId': d.STACK, 'LogicalResourceId': logical,
                    'ResourceType': 'AWS::Lambda::Version', 'PhysicalResourceId': old['physical_id'].rsplit(':', 1)[0] + ':' + number}}
        if operation in ('get-function-configuration', 'get-alias'):
            fid = next(fid for fid, info in j.FUNCTIONS.items() if info['name'] == kw['function_name'])
            old = self.p['old_versions'][fid]
            number = old['version'] if self.phase < 2 else str(int(old['version']) + 1)
            if operation == 'get-alias':
                return {'AliasArn': old['physical_id'].rsplit(':', 1)[0] + ':live', 'FunctionVersion': number}
            qualifier = kw.get('qualifier', '$LATEST')
            code = old['code_sha256'] if qualifier == old['version'] or not self.phase else self.p['asset']['sha256_base64']
            result = config(fid, old, qualifier, code)
            if self.not_ready and qualifier not in (old['version'], '$LATEST'):
                result['SnapStart']['OptimizationStatus'] = 'Off'
            return result
        if operation == 'head-object':
            return {'ServerSideEncryption': 'AES256', 'ContentLength': len(DATA),
                    'ChecksumSHA256': s.jar_asset(DATA)['sha256_base64'], 'VersionId': 'private-object-version'}
        if operation == 'put-object':
            if self.fail_upload:
                raise self.fail_upload
            return {'VersionId': 'private-object-version'}
        raise AssertionError((service, operation, kw))


def runner(p):
    cloud = Cloud(p)
    r = s.Runner(cloud.aws, sleep=lambda _: None)
    r.controls = lambda _: None
    return r, cloud


class SimpleDeploy(unittest.TestCase):
    def test_main_pinned_plan_publish_deploy_full_path(self):
        import os
        import sys
        p = plan()
        r, cloud = runner(p)
        cloud.phase, cloud.current = 1, copy.deepcopy(p['phase1'])
        r.actor = lambda _: None
        p['ready_versions'] = {fid: str(int(v['version']) + 1) for fid, v in p['old_versions'].items()}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template, jar, state = root / 'source.json', root / 'app.jar', root / 'state'
            template.write_text(json.dumps(p['source']))
            jar.write_bytes(DATA)
            (root / ('asset.' + 'e' * 64 + '.jar')).write_bytes(DATA)
            with patch.object(s, 'context', return_value=({}, SOURCE)), patch.object(s, 'Runner', return_value=r), \
                    patch('select_application.select', return_value=(root, 'b' * 40)), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '123'}):
                for mode in ('plan', 'publish', 'deploy'):
                    with patch.object(sys, 'argv', ['simple_deploy.py', mode, '--state', str(state),
                                                  '--template', str(template), '--jar', str(jar)]):
                        s.main()
                    if mode == 'plan':
                        saved = json.loads((state / 'plan.json').read_text())['plan']
                        self.assertEqual(saved['application_commit'], 'b' * 40)
                        self.assertEqual(saved['source_commit'], SOURCE)
                        self.assertTrue(all('new_version' in v for v in saved['old_versions'].values()))
                self.assertTrue((state / 'complete.json').is_file())
            writes = [op for _, op, _ in cloud.calls if op in ('put-object', 'create-change-set', 'execute-change-set')]
            self.assertEqual(writes, ['create-change-set', 'execute-change-set'])
            self.assertEqual(cloud.current, p['phase2'])

    def test_phase1_alias_read_failure_or_version_hash_mismatch_never_writes(self):
        for failure in ('get-alias', 'hash'):
            p = plan()
            cloud = Cloud(p)
            cloud.phase, cloud.current = 1, copy.deepcopy(p['phase1'])
            def aws(service, operation, **kwargs):
                if failure == 'get-alias' and operation == 'get-alias':
                    raise subprocess.CalledProcessError(255, 'aws', stderr='AccessDenied')
                result = cloud.aws(service, operation, **kwargs)
                if failure == 'hash' and operation == 'get-function-configuration' and kwargs.get('qualifier') not in (
                        '$LATEST', None, *[v['version'] for v in p['old_versions'].values()]):
                    result['CodeSha256'] = 'incorrect'
                return result
            r = s.Runner(aws, sleep=lambda _: None)
            r.controls = lambda _: None
            continued = s.phase1_plan(p['source'], p['phase1'], p['asset'], p['old_versions'], SOURCE)
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, self.assertRaises((ValueError, subprocess.CalledProcessError)):
                r.deploy({'directory': Path(temporary), 'plan': continued}, {})
            self.assertFalse(any(op in ('put-object', 'create-change-set', 'execute-change-set') for _, op, _ in cloud.calls))

    def test_completed_phase1_creates_and_executes_only_new_phase2(self):
        p = plan()
        r, cloud = runner(p)
        cloud.phase, cloud.current = 1, copy.deepcopy(p['phase1'])
        old = r.observe(p['phase1'], versions=p['versions'])
        continued = s.phase1_plan(p['source'], p['phase1'], p['asset'], old, SOURCE)
        self.assertEqual(continued['phase2'], p['phase2'])
        cloud.p = continued
        with tempfile.TemporaryDirectory() as temporary:
            r.deploy({'directory': Path(temporary), 'plan': continued}, {})
        self.assertEqual(cloud.current, p['phase2'])
        writes = [(op, kw) for _, op, kw in cloud.calls if op in ('put-object', 'create-change-set', 'execute-change-set')]
        self.assertEqual([op for op, _ in writes], ['create-change-set', 'execute-change-set'])
        self.assertEqual(writes[0][1]['change_set_name'], s.operation_name(SOURCE, 2))
        self.assertEqual(continued['ready_versions'], {fid: str(int(v['version'])+1) for fid, v in p['old_versions'].items()})

    def test_phase1_recognition_rejects_wrong_artifact_extra_versions_and_scope(self):
        p = plan()
        cases = []
        altered = copy.deepcopy(p['phase1'])
        altered['Resources'][next(iter(j.FUNCTIONS))]['Properties']['Code']['S3ObjectVersion'] = 'other'
        cases.append(altered)
        altered = copy.deepcopy(p['phase1'])
        altered['Resources']['ExtraVersion'] = copy.deepcopy(next(v for v in altered['Resources'].values() if v['Type'] == 'AWS::Lambda::Version'))
        cases.append(altered)
        altered = copy.deepcopy(p['phase1'])
        altered['Resources'][next(iter(j.FUNCTIONS))]['Properties']['Timeout'] = 999
        cases.append(altered)
        altered = copy.deepcopy(p['phase1'])
        altered['Resources'][next(iter(p['versions'].values()))['new_logical']]['Properties']['CodeSha256'] = 'bad'
        cases.append(altered)
        for live in cases + [p['baseline'], p['phase2']]:
            with self.subTest(live=cases.index(live) if live in cases else 'not-phase1'), self.assertRaises((ValueError, KeyError)):
                s.phase1_plan(p['source'], live, p['asset'], p['old_versions'], SOURCE)
        with self.assertRaises(ValueError):
            s.phase1_plan(p['source'], p['phase1'], {**p['asset'], 'sha256_base64': 'wrong'}, p['old_versions'], SOURCE)

    def test_phase1_continuation_still_rejects_not_ready_or_prior_operation(self):
        p = plan()
        r, cloud = runner(p)
        cloud.phase, cloud.current = 1, copy.deepcopy(p['phase1'])
        old = r.observe(p['phase1'], versions=p['versions'])
        continued = s.phase1_plan(p['source'], p['phase1'], p['asset'], old, SOURCE)
        cloud.not_ready = True
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            r.deploy({'directory': Path(temporary), 'plan': continued}, {})
        cloud.not_ready = False
        cloud.sets[s.operation_name(SOURCE, 2)] = {'ChangeSetId': 'existing'}
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            r.deploy({'directory': Path(temporary), 'plan': continued}, {})
        self.assertFalse(any(op in ('put-object', 'create-change-set', 'execute-change-set') for _, op, _ in cloud.calls))

    def test_stack_waiter_ignores_deleted_changeset_and_checks_exact_template(self):
        p = plan()
        r, cloud = runner(p)
        before = {'StackStatus': 'UPDATE_ROLLBACK_COMPLETE', 'LastUpdatedTime': 'old'}
        states = [before, {'StackStatus': 'UPDATE_IN_PROGRESS', 'LastUpdatedTime': 'new'},
                  {'StackStatus': 'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS', 'LastUpdatedTime': 'new'},
                  {'StackStatus': 'UPDATE_COMPLETE', 'LastUpdatedTime': 'new'}]
        r.stack = lambda: states.pop(0)
        r.template = lambda: p['phase1']
        r.describe = lambda _: self.fail('Must not poll a change set after execution')
        self.assertEqual(r.wait_stack(p['phase1'], before)['StackStatus'], 'UPDATE_COMPLETE')

    def test_stack_waiter_rejects_rollback_and_wrong_completed_template(self):
        p = plan()
        before = {'StackStatus': 'UPDATE_COMPLETE', 'LastUpdatedTime': 'old'}
        for status in ('UPDATE_ROLLBACK_IN_PROGRESS', 'UPDATE_ROLLBACK_COMPLETE',
                       'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS', 'UPDATE_ROLLBACK_FAILED',
                       'DELETE_IN_PROGRESS', 'UPDATE_COMPLETE'):
            r, _ = runner(p)
            r.stack = lambda status=status: {'StackStatus': status, 'LastUpdatedTime': 'new'}
            r.template = lambda: p['baseline']
            with self.subTest(status=status), self.assertRaises(ValueError):
                r.wait_stack(p['phase1'], before)

    def test_stack_waiter_never_accepts_stale_baseline_or_timeout_as_success(self):
        p = plan()
        r, _ = runner(p)
        before = {'StackStatus': 'UPDATE_COMPLETE', 'LastUpdatedTime': 'old'}
        r.stack = lambda: before
        r.template = lambda: p['baseline']
        with self.assertRaisesRegex(ValueError, 'still pending'):
            r.wait_stack(p['phase1'], before)

    def test_completed_rollback_requires_exact_baseline_and_ready_runtime(self):
        p = plan()
        r, cloud = runner(p)
        original = r.stack
        r.stack = lambda: {**original(), 'StackStatus': 'UPDATE_ROLLBACK_COMPLETE'}
        r.fresh(SOURCE)
        self.assertEqual(set(r.observe(p['baseline'])), set(j.FUNCTIONS))
        changed = copy.deepcopy(p['baseline'])
        changed['Description'] = 'unexpected drift'
        with self.assertRaises(ValueError):
            r.observe(changed)
        cloud.phase = 1  # Latest code no longer equals the old published version.
        with self.assertRaises(ValueError):
            r.observe(p['baseline'])

    def test_incomplete_failed_or_cleanup_stack_never_retried(self):
        p = plan()
        for status in ('UPDATE_IN_PROGRESS', 'UPDATE_ROLLBACK_IN_PROGRESS',
                       'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS', 'UPDATE_ROLLBACK_FAILED',
                       'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS', 'ROLLBACK_COMPLETE'):
            r, _ = runner(p)
            r.stack = lambda status=status: {'StackStatus': status}
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    r.fresh(SOURCE)
                with self.assertRaises(ValueError):
                    r.observe(p['baseline'])

    def test_rollback_complete_does_not_bypass_existing_operation_gate(self):
        p = plan()
        r, cloud = runner(p)
        original = r.stack
        r.stack = lambda: {**original(), 'StackStatus': 'UPDATE_ROLLBACK_COMPLETE'}
        cloud.sets[s.operation_name(SOURCE, 1)] = changes(p, 1)
        with self.assertRaisesRegex(ValueError, 'Prior source operation exists'):
            r.fresh(SOURCE)

    def test_plan_is_only_code_versions_aliases_and_preserves_metadata(self):
        p = plan()
        for phase in ('phase1', 'phase2'):
            self.assertEqual(p[phase]['Resources']['CDKMetadata'], p['baseline']['Resources']['CDKMetadata'])
        for fid, info in j.FUNCTIONS.items():
            self.assertEqual(p['phase1']['Resources'][info['alias']], p['baseline']['Resources'][info['alias']])
            old = p['versions'][fid]['old_logical']
            self.assertIn(old, p['phase1']['Resources'])
            self.assertNotIn(old, p['phase2']['Resources'])
            self.assertEqual(p['phase1']['Resources'][old]['DeletionPolicy'], 'Retain')

    def test_every_non_app_source_delta_is_rejected(self):
        source, live, asset, old = fixture()
        for logical, resource in source['Resources'].items():
            bad = copy.deepcopy(source)
            bad['Resources'][logical].setdefault('Properties', {})['Unsupported'] = True
            with self.subTest(resource=logical), self.assertRaises((ValueError, KeyError)):
                s.derive(bad, live, asset, old, SOURCE)

    def test_same_jar_is_noop_without_publish_or_fresh_versions(self):
        source, live, asset, old = fixture()
        for value in old.values():
            value['code_sha256'] = asset['sha256_base64']
        p = s.derive(source, live, asset, old, SOURCE)
        self.assertTrue(p['noop'])
        self.assertNotIn('phase1', p)

    def test_live_versions_are_observed_not_hardcoded_and_environment_not_saved(self):
        p = plan()
        r, _ = runner(p)
        old = r.observe(p['baseline'])
        self.assertEqual({v['version'] for v in old.values()}, {'101', '207'})
        self.assertNotIn('must-not-be-persisted', json.dumps(old))
        self.assertTrue(all('configuration_sha256' in v for v in old.values()))

    def test_full_release_succeeds_when_executed_change_sets_disappear(self):
        p = plan()
        cloud = Cloud(p)
        def aws(service, operation, **arguments):
            response = cloud.aws(service, operation, **arguments)
            if operation == 'execute-change-set':
                cloud.sets.clear()
            return response
        r = s.Runner(aws, sleep=lambda _: None)
        r.controls = lambda _: None
        with tempfile.TemporaryDirectory() as temporary:
            r.deploy({'directory': Path(temporary), 'plan': p}, {})
            self.assertTrue((Path(temporary) / 'complete.json').exists())
        self.assertEqual(cloud.current, p['phase2'])
        self.assertEqual([x[1] for x in cloud.calls].count('execute-change-set'), 2)

    def test_full_fake_release_has_two_creates_and_executes_and_one_jar(self):
        p = plan()
        r, cloud = runner(p)
        with tempfile.TemporaryDirectory() as temporary:
            state = {'directory': Path(temporary), 'plan': p}
            receipt = r.publish(DATA, state)
            self.assertEqual(receipt, p['asset'])
            r.deploy(state, {})
            self.assertTrue((Path(temporary) / 'complete.json').exists())
        names = [x[1] for x in cloud.calls]
        self.assertEqual(names.count('put-object'), 1)
        self.assertEqual(names.count('create-change-set'), 2)
        self.assertEqual(names.count('execute-change-set'), 2)
        self.assertEqual(names.count('describe-events'), 4)
        for service, operation, args in cloud.calls:
            if operation == 'create-change-set':
                self.assertIn('template_body', args)
                self.assertNotIn('template_url', args)
                self.assertEqual(args['role_arn'], d.EXECUTOR)
        self.assertEqual(cloud.current, p['phase2'])

    def test_new_jar_never_heads_missing_key_and_412_reuses(self):
        for conflict in (False, True):
            p = plan()
            r, cloud = runner(p)
            if conflict:
                cloud.fail_upload = subprocess.CalledProcessError(255, 'aws', stderr='(PreconditionFailed)')
            with tempfile.TemporaryDirectory() as temporary:
                r.publish(DATA, {'directory': Path(temporary), 'plan': p})
            self.assertEqual(cloud.calls[0][1], 'put-object')
            self.assertEqual([x[1] for x in cloud.calls].count('put-object'), 1)
            self.assertEqual(cloud.calls[0][2]['if_none_match'], '*')
            self.assertEqual(cloud.calls[0][2]['storage_class'], 'STANDARD')

    def test_upload_uncertainty_never_retries_or_reads_as_success(self):
        for message in ('(409)', 'timeout', '(403)'):
            p = plan()
            r, cloud = runner(p)
            cloud.fail_upload = subprocess.CalledProcessError(255, 'aws', stderr=message)
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                r.publish(DATA, {'directory': Path(temporary), 'plan': p})
            self.assertEqual([x[1] for x in cloud.calls], ['put-object'])

    def test_create_and_execute_uncertainty_stop_and_fresh_run_does_not_replay(self):
        for action in ('create', 'execute'):
            p = plan()
            r, cloud = runner(p)
            setattr(cloud, 'fail_' + action, TimeoutError('unknown outcome'))
            with tempfile.TemporaryDirectory() as temporary:
                state = {'directory': Path(temporary), 'plan': p}
                with self.assertRaises(TimeoutError):
                    r.deploy(state, {})
                with self.assertRaises(ValueError):
                    r.deploy(state, {})
                # A different local directory has no power to reissue the deterministic name.
                with tempfile.TemporaryDirectory() as fresh, self.assertRaises(ValueError):
                    r.deploy({'directory': Path(fresh), 'plan': p}, {})
            self.assertEqual([x[1] for x in cloud.calls].count('create-change-set'), 1)
            self.assertEqual([x[1] for x in cloud.calls].count('execute-change-set'), int(action == 'execute'))

    def test_no_alias_phase_without_active_snapstart(self):
        p = plan()
        r, cloud = runner(p)
        cloud.not_ready = True
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            r.deploy({'directory': Path(temporary), 'plan': p}, {})
        self.assertEqual([x[1] for x in cloud.calls].count('create-change-set'), 1)
        self.assertEqual([x[1] for x in cloud.calls].count('execute-change-set'), 1)

    def test_enhanced_context_unknown_resource_retention_and_own_response_guards(self):
        p = plan()
        p['ready_versions'] = {fid: str(int(v['version']) + 1) for fid, v in p['old_versions'].items()}
        for phase in (1, 2):
            cs = changes(p, phase)
            created = {'Id': cs['ChangeSetId'], 'StackId': d.STACK}
            s.validate_changes(p, phase, cs, p[f'phase{phase}'], created)
            faults = ['context', 'missing', 'extra', 'replacement', 'receipt', 'template', 'before']
            if phase == 2:
                faults += ['retention', 'target']
            for fault in faults:
                bad, result, template = copy.deepcopy(cs), copy.deepcopy(created), copy.deepcopy(p[f'phase{phase}'])
                change = bad['Changes'][0]['ResourceChange']
                if fault == 'context':
                    context = json.loads(change['AfterContext'])
                    context['Properties']['UnexpectedConfig'] = True
                    change['AfterContext'] = json.dumps(context)
                if fault == 'before':
                    context = json.loads(change['BeforeContext'])
                    context['Properties']['Code' if phase == 1 else 'FunctionVersion'] = 'wrong'
                    change['BeforeContext'] = json.dumps(context)
                if fault == 'missing': change.pop('AfterContext')
                if fault == 'extra': change['LogicalResourceId'] = 'DataOrHelper'
                if fault == 'replacement': change['Replacement'] = 'Conditional'
                if fault == 'receipt': result['Id'] = 'unproven'
                if fault == 'template': template['Unexpected'] = True
                if fault == 'retention': bad['Changes'][1]['ResourceChange']['PolicyAction'] = 'Delete'
                if fault == 'target':
                    context = json.loads(change['AfterContext'])
                    context['Properties']['FunctionVersion'] = '999'
                    change['AfterContext'] = json.dumps(context)
                with self.subTest(phase=phase, fault=fault), self.assertRaises(ValueError):
                    s.validate_changes(p, phase, bad, template, result)

    def test_current_applied_operation_is_checked_when_reported_by_stack(self):
        p = plan()
        r, cloud = runner(p)
        other = changes(p, 1)
        cloud.sets[other['ChangeSetName']] = other
        r.stack = lambda: {'StackStatus': 'UPDATE_COMPLETE', 'ChangeSetId': other['ChangeSetId']}
        other['ExecutionStatus'] = 'EXECUTE_IN_PROGRESS'
        with self.assertRaises(ValueError): r.fresh('b' * 40)
        other['ExecutionStatus'] = 'AVAILABLE'
        r.fresh('b' * 40)
        other['ExecutionStatus'] = 'EXECUTE_COMPLETE'
        r.fresh('b' * 40)

    def test_staged_jar_must_match_source_and_build(self):
        source, _, _, _ = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            template = Path(temporary) / 'stack.template.json'
            with self.assertRaises(ValueError): s.check_staged_jar(source, template, DATA)
            (template.parent / ('asset.' + 'e' * 64 + '.jar')).write_bytes(DATA)
            s.check_staged_jar(source, template, DATA)
            with self.assertRaises(ValueError): s.check_staged_jar(source, template, b'other')

    def test_inline_limit_is_checked_before_cloudformation(self):
        source, live, asset, old = fixture()
        source['Description'] = live['Description'] = 'x' * 51200
        with self.assertRaises(ValueError): s.derive(source, live, asset, old, SOURCE)

    def test_push_context_is_frozen_to_event_not_moving_remote_main(self):
        import os
        env = {'GITHUB_SHA': SOURCE, 'GITHUB_REPOSITORY': s.REPO, 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_WORKFLOW_REF': s.REPO + '/' + s.WORKFLOW + '@refs/heads/main',
               'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_EVENT_NAME': 'push', 'GITHUB_ACTOR_ID': '123',
               'GITHUB_EVENT_PATH': 'unused'}
        event = {'repository': {'full_name': s.REPO}, 'sender': {'id': 123},
                 'ref': 'refs/heads/main', 'after': SOURCE}
        with patch.dict(os.environ, env), patch.object(d, 'java_active', return_value={}), \
                patch.object(d, 'load', return_value=event), \
                patch('select_application.select', return_value=(Path('.'), SOURCE)), \
                patch.object(s.subprocess, 'check_output', return_value=SOURCE + '\n') as git:
            self.assertEqual(s.context()[1], SOURCE)
            self.assertEqual(git.call_args.args[0], ['git', 'rev-parse', 'HEAD'])
            self.assertEqual(git.call_count, 1)
            event['after'] = 'b' * 40
            with self.assertRaises(ValueError): s.context()

    def test_rerun_after_prewrite_failure_is_allowed_but_prior_operation_still_blocks(self):
        import os
        env = {'GITHUB_SHA': SOURCE, 'GITHUB_REPOSITORY': s.REPO, 'GITHUB_REF': 'refs/heads/main',
               'GITHUB_WORKFLOW_REF': s.REPO + '/' + s.WORKFLOW + '@refs/heads/main',
               'GITHUB_EVENT_NAME': 'push', 'GITHUB_ACTOR_ID': '123', 'GITHUB_EVENT_PATH': 'unused'}
        event = {'repository': {'full_name': s.REPO}, 'sender': {'id': 123},
                 'ref': 'refs/heads/main', 'after': SOURCE}
        with patch.dict(os.environ, env), patch.object(d, 'java_active', return_value={}), \
                patch.object(d, 'load', return_value=event), \
                patch('select_application.select', return_value=(Path('.'), SOURCE)), \
                patch.object(s.subprocess, 'check_output', return_value=SOURCE + '\n'):
            for attempt in ('1', '2', '17'):
                os.environ['GITHUB_RUN_ATTEMPT'] = attempt
                self.assertEqual(s.context()[1], SOURCE)
                r, cloud = runner(plan())
                r.fresh(SOURCE)  # A pre-build failure left no AWS operation.
                self.assertFalse(any(call[1] in ('put-object', 'create-change-set', 'execute-change-set') for call in cloud.calls))
                prior = changes(plan(), 1)
                cloud.sets[prior['ChangeSetName']] = prior
                with self.assertRaises(ValueError): r.fresh(SOURCE)
            for attempt in ('', '0', '-1', '01', '2.0', 'true'):
                os.environ['GITHUB_RUN_ATTEMPT'] = attempt
                with self.assertRaises(ValueError): s.context()
            os.environ['GITHUB_RUN_ATTEMPT'] = '2'
            event['after'] = 'b' * 40
            with self.assertRaises(ValueError): s.context()

    def test_failure_diagnostics_are_static_and_do_not_echo_private_values(self):
        secret = 'PRIVATE-ENV-TOKEN-SENTINEL'
        with patch.dict(s.DIAGNOSTIC, {'stage': 'deploy', 'operation': 'execute-change-set'}):
            error = subprocess.CalledProcessError(1, ['aws', secret], stderr=secret)
            message = s.failure_message(error)
            self.assertIn('stage=deploy operation=execute-change-set reason=uncertain-write', message)
            self.assertNotIn(secret, message)
            self.assertIn('version-not-ready', s.failure_message(ValueError('Version not Active with SnapStart On')))
            self.assertNotIn(secret, s.failure_message(ValueError(secret)))

    def test_operation_identity_is_stable_and_different_between_phases(self):
        self.assertEqual(s.operation_name(SOURCE, 1), s.operation_name(SOURCE, 1))
        self.assertNotEqual(s.operation_name(SOURCE, 1), s.operation_name(SOURCE, 2))
        self.assertNotEqual(s.operation_name(SOURCE, 1), s.operation_name('b' * 40, 1))


if __name__ == '__main__':
    unittest.main()

