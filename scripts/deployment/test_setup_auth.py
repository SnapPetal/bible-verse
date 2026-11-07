import copy
import json
import unittest
from pathlib import Path
import setup_auth as s
import bible_deploy as d


def context():
    return dict(repository='SnapPetal/bible-verse', ref='refs/heads/main', event='workflow_dispatch',
                sha='a'*40, head='a'*40, main='a'*40, run_id='17')


class Probe(unittest.TestCase):
    def setUp(self):
        self.config = {key: False for key in s.FLAGS}
        self.mode = 'deploy'
        self.calls = []
        self.responses = {
            'describe-stacks': {'Stacks': [{'StackId': d.STACK, 'RoleARN': d.EXECUTOR,
                                            'StackStatus': 'UPDATE_COMPLETE', 'Outputs': ['PRIVATE_SENTINEL']}]},
            'get-bucket-versioning': {'Status': 'Enabled'},
            'get-bucket-encryption': {'ServerSideEncryptionConfiguration': {'Rules': [
                {'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}, 'BucketKeyEnabled': False}]}},
            'get-public-access-block': {'PublicAccessBlockConfiguration': {
                key: True for key in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')}},
            'get-bucket-ownership-controls': {'OwnershipControls': {'Rules': [{'ObjectOwnership': 'BucketOwnerEnforced'}]}},
        }

    def aws(self, service, operation, **kwargs):
        self.calls.append((service, operation, kwargs))
        if operation == 'get-caller-identity':
            return {'Account': d.ACCOUNT, 'Arn': f'arn:aws:sts::{d.ACCOUNT}:assumed-role/{s.ROLES[self.mode]}/bible-setup-{self.mode}-17'}
        return copy.deepcopy(self.responses[operation])

    def run_probe(self):
        return s.probe(self.mode, self.config, context(), self.aws)

    def test_disabled_deploy_success_projects_only_booleans(self):
        original = copy.deepcopy(self.config)
        result = self.run_probe()
        self.assertTrue(all(value is True for value in result.values()))
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))
        self.assertEqual(self.config, original)
        self.assertEqual(len(self.calls), 6)
        for service, operation, kwargs in self.calls:
            if service == 's3api':self.assertEqual(kwargs, {'bucket': d.BUCKET, 'expected_bucket_owner': d.ACCOUNT})
            if service == 'cloudformation':self.assertEqual(kwargs, {'stack_name': d.STACK})

    def test_entry_and_publisher_minimum_reads(self):
        for mode in ['entry-deploy', 'entry-publisher', 'publisher']:
            self.mode = mode;self.calls = [];self.run_probe()
            self.assertEqual([x[1] for x in self.calls], ['get-caller-identity'] + (['get-bucket-versioning'] if mode == 'publisher' else []))

    def test_enabled_or_missing_flags_block_before_any_call(self):
        for key in s.FLAGS:
            for bad in [True, None, 0, 'false']:
                self.config = {k: False for k in s.FLAGS};self.config[key] = bad;self.calls = []
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):self.run_probe()
                self.assertEqual(self.calls, [])

    def test_wrong_source_blocks_before_any_call(self):
        for key, value in [('repository', 'other/repo'), ('ref', 'refs/heads/other'), ('event', 'push'), ('sha', 'bad'), ('head', 'b'*40), ('main', 'b'*40), ('run_id', 'x')]:
            ctx = context();ctx[key] = value;self.calls = []
            with self.subTest(key=key), self.assertRaises(ValueError):s.probe('deploy', self.config, ctx, self.aws)
            self.assertEqual(self.calls, [])

    def test_wrong_actor_account_or_session_blocks(self):
        good = self.aws('sts', 'get-caller-identity')
        for patch in [{'Account': '000000000000'}, {'Arn': good['Arn'].replace('/BibleVerseCdkDeploy/', '/Other/')}, {'Arn': good['Arn'].replace('-17', '-18')}, {'Arn': good['Arn'] + '/extra'}]:
            calls = []
            def bad(service, operation, **kw):
                calls.append(operation);return {**good, **patch}
            with self.subTest(patch=patch), self.assertRaises(ValueError):s.probe('deploy', self.config, context(), bad)
            self.assertEqual(calls, ['get-caller-identity'])

    def test_wrong_stack_executor_status_or_incomplete_response(self):
        good = copy.deepcopy(self.responses['describe-stacks'])
        cases = [{'Stacks': []}, {'Stacks': good['Stacks']*2}]
        for key, value in [('StackId', 'other'), ('RoleARN', 'shared'), ('StackStatus', 'UPDATE_IN_PROGRESS'), ('StackStatus', 'UPDATE_ROLLBACK_COMPLETE')]:
            x = copy.deepcopy(good);x['Stacks'][0][key] = value;cases.append(x)
        for x in cases:
            self.responses['describe-stacks'] = x
            with self.subTest(x=x), self.assertRaises(ValueError):self.run_probe()

    def test_wrong_bucket_metadata_blocks(self):
        cases = [('get-bucket-versioning', {'Status': 'Suspended'}), ('get-bucket-encryption', {}),
                 ('get-bucket-encryption', {'ServerSideEncryptionConfiguration': {'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'aws:kms'}}]}}),
                 ('get-public-access-block', {'PublicAccessBlockConfiguration': {'BlockPublicAcls': True}}),
                 ('get-bucket-ownership-controls', {'OwnershipControls': {'Rules': [{'ObjectOwnership': 'ObjectWriter'}]}})]
        for op, value in cases:
            old = self.responses[op];self.responses[op] = value
            with self.subTest(op=op), self.assertRaises(ValueError):self.run_probe()
            self.responses[op] = old

    def test_workflow_no_inputs_writes_uploads_or_release_path(self):
        text = (Path(__file__).resolve().parents[2]/'.github/workflows/setup-auth.yml').read_text()
        for forbidden in ['inputs:', 'upload-artifact', 'java_adapter.py', 'put-object', 'cdk deploy', 'GetTemplate']:
            self.assertNotIn(forbidden, text)
        for expected in ['timeout-minutes: 5', 'environment: production', 'role-chaining: false', 'setup_auth.py gate']:
            self.assertIn(expected, text)
        self.assertEqual(text.count('unset-current-credentials: true'), 2)
        self.assertEqual(text.count('setup_auth.py '), 5)


if __name__ == '__main__':
    unittest.main()
