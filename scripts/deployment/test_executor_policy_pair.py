import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import executor_policy_pair as p


def fixture_activation():
    # Synthetic unit-test observation only; never written into activation.json.
    evidence = {key: copy.deepcopy(p.EXPECTED[key]) for key in (
        'executor_role_arn', 'inline_policy_name', 'inline_sha256', 'managed_policy_arn',
        'managed_sha256', 'managed_attachment_roles', 'managed_attachment_users', 'managed_attachment_groups')}
    evidence.update(purpose='operator-verified-executor-policy-pair', receipt_sha256='f'*64,
                    managed_default_version='v1', executor_managed_policy_arns=[p.GUARD],
                    executor_inline_policy_names=['FoundationStagedPolicy'], executor_permissions_boundary=None)
    return {'executor_policy_pair_sha256': p.digest(p.EXPECTED),
            'executor_policy_operator_verification': evidence}


class Pairing(unittest.TestCase):
    def setUp(self):
        self.inline = json.loads((p.DIRECTORY/'BibleVerseCfnExecution.json').read_text())
        self.guard = json.loads((p.DIRECTORY/'BibleVerseProviderReadGuard.json').read_text())

    def test_exact_frozen_pair_and_limits(self):
        self.assertEqual(p.verify_source_pair(), p.digest(p.EXPECTED))
        self.assertEqual(len(p.canonical(self.inline)), 9554)
        self.assertEqual(len(p.canonical(self.guard)), 3386)
        self.assertEqual(p.verify_activation(fixture_activation()), p.digest(p.EXPECTED))

    def test_unsupported_storage_class_action_globally_denied(self):
        action = 's3:GetBucketDefaultStorageClass'
        for policy in (self.inline, self.guard):
            self.assertNotIn(action, json.dumps(policy))
        ceiling = [s for s in self.guard['Statement'] if 'NotAction' in s]
        self.assertEqual(len(ceiling), 1)
        self.assertEqual(ceiling[0]['Effect'], 'Deny')
        self.assertEqual(ceiling[0]['Resource'], '*')
        self.assertNotIn(action, ceiling[0]['NotAction'])
        self.assertNotIn('Condition', ceiling[0])

    def test_missing_changed_or_swapped_policy_rejected(self):
        for inline, managed in [({}, self.guard), (self.inline, {}), (self.guard, self.inline)]:
            with self.assertRaises(ValueError):p.validate_pair(p.EXPECTED, inline, managed)
        for kind in ['inline', 'guard']:
            inline = copy.deepcopy(self.inline);guard = copy.deepcopy(self.guard)
            target = inline if kind == 'inline' else guard
            target['Statement'] = [s for s in target['Statement'] if not (s.get('Effect') == 'Deny' and 'Condition' in s)]
            with self.assertRaises(ValueError):p.validate_pair(p.EXPECTED, inline, guard)

    def test_missing_guard_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ['executor-pair.json', 'BibleVerseCfnExecution.json']:
                (root/name).write_bytes((p.DIRECTORY/name).read_bytes())
            with self.assertRaises(FileNotFoundError):p.verify_source_pair(root)

    def test_manifest_purpose_and_attachment_negatives(self):
        for key, value in [('purpose', 'another-purpose'), ('executor_role_arn', 'shared'),
                           ('managed_policy_arn', 'other'), ('managed_attachment_roles', [p.ROLE, 'other']),
                           ('managed_attachment_users', ['user']), ('managed_attachment_groups', ['group']),
                           ('retention', {}), ('managed_file', '../../other.json'), ('extra', True)]:
            manifest = copy.deepcopy(p.EXPECTED);manifest[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):p.validate_pair(manifest, self.inline, self.guard)

    def test_activation_requires_operator_evidence_not_boolean(self):
        for config in [{}, {'executor_permissions_approved': True},
                       {'executor_policy_pair_sha256': p.digest(p.EXPECTED)},
                       {**fixture_activation(), 'executor_policy_pair_sha256': '0'*64}]:
            with self.assertRaises(ValueError):p.verify_activation(config)

    def test_operator_wrong_hash_role_attachment_or_version_rejected(self):
        cases = [('purpose', 'caller-success'), ('receipt_sha256', '0'*64), ('receipt_sha256', 'missing'),
                 ('inline_sha256', p.GUARD_SHA), ('managed_sha256', p.INLINE_SHA), ('executor_role_arn', 'other'),
                 ('managed_attachment_roles', []), ('managed_attachment_roles', [p.ROLE, 'other']),
                 ('managed_attachment_users', ['user']), ('managed_attachment_groups', ['group']),
                 ('executor_managed_policy_arns', []), ('executor_managed_policy_arns', [p.GUARD, 'other']),
                 ('executor_inline_policy_names', ['FoundationStagedPolicy', 'other']),
                 ('executor_permissions_boundary', 'other'), ('managed_default_version', ''),
                 ('managed_default_version', 'v0'), ('extra', 'raw-event')]
        for key, value in cases:
            config = fixture_activation();config['executor_policy_operator_verification'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):p.verify_activation(config)

    def test_missing_operator_fields_rejected(self):
        for key in fixture_activation()['executor_policy_operator_verification']:
            config = fixture_activation();del config['executor_policy_operator_verification'][key]
            with self.subTest(key=key), self.assertRaises(ValueError):p.verify_activation(config)

    def test_offline_verify_checks_pair_even_disabled(self):
        import bible_deploy as d
        with patch('sys.argv', ['bible_deploy.py', 'verify']), patch.object(d, 'load', return_value={'enabled': False}), patch.object(p, 'verify_source_pair', side_effect=ValueError('Missing guard')) as checked, patch.object(d, 'aws') as aws:
            with self.assertRaises(ValueError):d.main()
            checked.assert_called_once();aws.assert_not_called()

    def test_managed_guard_has_standalone_principal_region_and_global_fences(self):
        statements = self.guard['Statement']
        self.assertTrue(any(s.get('NotAction') for s in statements))
        for key in ['aws:PrincipalArn', 'aws:RequestedRegion']:
            self.assertTrue(any(s['Effect'] == 'Deny' and s.get('Action') == '*' and
                                key in json.dumps(s.get('Condition', {})) for s in statements))
        self.assertFalse(any(s.get('Action') == '*' and s['Effect'] == 'Allow' for s in statements))


if __name__ == '__main__':
    unittest.main()
