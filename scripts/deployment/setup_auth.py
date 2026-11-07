#!/usr/bin/env python3
"""Read-only setup probe. No activation, templates, Lambda configs, uploads or writes."""
import argparse
import json
import os
import re
import subprocess
import sys
import bible_deploy as d

FLAGS = ('enabled', 'java_release_enabled', 'migration_adapter_enabled',
         'private_checkpoint_upload_approved', 'public_checkpoint_reference_sharing_approved')
ROLES = {'entry-deploy': 'GitHubActionsBibleVerseProdDeploy',
         'deploy': 'BibleVerseCdkDeploy',
         'entry-publisher': 'GitHubActionsBibleVerseProdDeploy',
         'publisher': 'BibleVerseCdkFilePublisher'}


def gate(config, context):
    d.need(all(config.get(key) is False for key in FLAGS), 'Setup requires all mutation flags disabled')
    d.need(context.get('repository') == 'SnapPetal/bible-verse' and
           context.get('ref') == 'refs/heads/main' and
           context.get('event') == 'workflow_dispatch', 'Wrong setup source context')
    commit = context.get('sha', '')
    d.need(re.fullmatch('[0-9a-f]{40}', commit) is not None and
           context.get('head') == commit and context.get('main') == commit,
           'Setup requires exact current main checkout')
    d.need(re.fullmatch('[1-9][0-9]*', context.get('run_id', '')) is not None,
           'Invalid setup run identity')


def probe(mode, config, context, aws):
    gate(config, context)
    d.need(mode in ROLES, 'Unknown read-only setup step')
    identity = aws('sts', 'get-caller-identity')
    expected = f'arn:aws:sts::{d.ACCOUNT}:assumed-role/{ROLES[mode]}/bible-setup-{mode}-{context["run_id"]}'
    d.need(identity.get('Account') == d.ACCOUNT and identity.get('Arn') == expected,
           'Wrong setup account, role or session')
    result = {'read_only': True, 'all_mutation_flags_disabled': True, 'identity_verified': True}
    if mode.startswith('entry-'):
        return result
    if mode == 'deploy':
        stacks = aws('cloudformation', 'describe-stacks', stack_name=d.STACK).get('Stacks')
        d.need(isinstance(stacks, list) and len(stacks) == 1, 'Incomplete exact stack observation')
        stack = stacks[0]
        d.need(stack.get('StackId') == d.STACK and stack.get('RoleARN') == d.EXECUTOR and
               stack.get('StackStatus') == 'UPDATE_COMPLETE', 'Wrong app stack, executor or status')
        result['dedicated_app_verified'] = True
    versioning = aws('s3api', 'get-bucket-versioning', bucket=d.BUCKET,
                     expected_bucket_owner=d.ACCOUNT)
    d.need(versioning.get('Status') == 'Enabled', 'Dedicated bucket is not versioned')
    result['bucket_versioning_verified'] = True
    if mode == 'publisher':
        return result
    encryption = aws('s3api', 'get-bucket-encryption', bucket=d.BUCKET,
                     expected_bucket_owner=d.ACCOUNT)
    rules = encryption.get('ServerSideEncryptionConfiguration', {}).get('Rules')
    d.need(isinstance(rules, list) and len(rules) == 1 and
           rules[0].get('ApplyServerSideEncryptionByDefault') == {'SSEAlgorithm': 'AES256'} and
           rules[0].get('BucketKeyEnabled', False) is False, 'Wrong dedicated bucket encryption')
    public = aws('s3api', 'get-public-access-block', bucket=d.BUCKET,
                 expected_bucket_owner=d.ACCOUNT).get('PublicAccessBlockConfiguration')
    d.need(public == {key: True for key in ('BlockPublicAcls', 'IgnorePublicAcls',
                                          'BlockPublicPolicy', 'RestrictPublicBuckets')},
           'Dedicated bucket public access not fully blocked')
    owner = aws('s3api', 'get-bucket-ownership-controls', bucket=d.BUCKET,
                expected_bucket_owner=d.ACCOUNT).get('OwnershipControls')
    d.need(owner == {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]},
           'Wrong dedicated bucket ownership')
    result.update(bucket_encryption_verified=True, bucket_public_block_verified=True,
                  bucket_ownership_verified=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['gate', *ROLES])
    args = parser.parse_args()
    try:
        sha = d.source_gate()
        config = d.load(d.CONFIG)
        context = {'repository': os.environ.get('GITHUB_REPOSITORY'),
                   'ref': os.environ.get('GITHUB_REF'), 'event': os.environ.get('GITHUB_EVENT_NAME'),
                   'sha': sha, 'head': sha, 'main': sha, 'run_id': os.environ.get('GITHUB_RUN_ID', '')}
        gate(config, context)
        if args.mode == 'gate':
            result = {'read_only': True, 'source_verified': True, 'all_mutation_flags_disabled': True}
        else:
            result = probe(args.mode, config, context, d.aws)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        # No raw AWS errors, stack properties or environment values enter public logs.
        print('Setup authentication verification failed; no mutation was attempted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
