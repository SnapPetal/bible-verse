"""Frozen source pairing and operator-postflight pins. No IAM/network access."""
import hashlib
import json
import re
from pathlib import Path

ROLE = 'arn:aws:iam::664759038511:role/BibleVerseCfnExecution'
GUARD = 'arn:aws:iam::664759038511:policy/BibleVerseProviderReadGuard'
INLINE_SHA = '49216639c29a04b4772f6b21b5b38aceff2f072272c1816ed0b39ff8807b36ad'
GUARD_SHA = 'bf3ed914ae15b43c13281937b0af467245a1cdb1afb4cdcc5810a2d4c94fdb91'
EXPECTED = {
    'schema': 1,
    'purpose': 'bible-two-app-java-executor-provider-read-guard',
    'executor_role_arn': ROLE,
    'inline_policy_name': 'FoundationStagedPolicy',
    'inline_file': 'BibleVerseCfnExecution.json',
    'inline_sha256': INLINE_SHA,
    'managed_policy_arn': GUARD,
    'managed_file': 'BibleVerseProviderReadGuard.json',
    'managed_sha256': GUARD_SHA,
    'managed_attachment_roles': [ROLE],
    'managed_attachment_users': [],
    'managed_attachment_groups': [],
    'retention': {'DeletionPolicy': 'Retain', 'UpdateReplacePolicy': 'Retain'},
    'foundation_candidate_sha256': '4c542c3306c87e7a31e241de02854eaaad5f47920dde71534a3d944880fb300e',
}
DIRECTORY = Path(__file__).resolve().parents[2]/'.infrastructure/deployment/release-policies'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def validate_pair(manifest, inline, managed):
    need(manifest == EXPECTED, 'Wrong executor policy pairing or purpose')
    need(digest(inline) == INLINE_SHA and digest(managed) == GUARD_SHA,
         'Missing or changed executor policy pair')
    for policy in (inline, managed):
        statements = policy['Statement']
        for condition in ({'ArnNotEquals': {'aws:PrincipalArn': ROLE}},
                          {'StringNotEquals': {'aws:RequestedRegion': 'us-east-1'}}):
            need(any(s.get('Effect') == 'Deny' and s.get('Action') == '*' and
                     s.get('Resource') == '*' and s.get('Condition') == condition
                     for s in statements), 'Missing principal or region fence')
    globals_ = [s for s in managed['Statement'] if 'NotAction' in s]
    need(len(globals_) == 1 and globals_[0]['Effect'] == 'Deny' and
         globals_[0]['Resource'] == '*', 'Managed global guard missing')
    allowed = {action for policy in (inline, managed) for s in policy['Statement']
               if s['Effect'] == 'Allow' for action in
               (s['Action'] if isinstance(s['Action'], list) else [s['Action']])}
    need(set(globals_[0]['NotAction']) == allowed, 'Global guard and paired capabilities differ')
    need(len(canonical(inline)) <= 10240 and len(canonical(managed)) <= 6144,
         'Executor policy size limit exceeded')
    return digest(manifest)


def verify_source_pair(directory=DIRECTORY):
    manifest = json.loads((directory/'executor-pair.json').read_text())
    inline = json.loads((directory/'BibleVerseCfnExecution.json').read_text())
    managed = json.loads((directory/'BibleVerseProviderReadGuard.json').read_text())
    return validate_pair(manifest, inline, managed)


def verify_activation(config):
    pair_hash = verify_source_pair()
    need(config.get('executor_policy_pair_sha256') == pair_hash,
         'Activation does not pin reviewed executor pair')
    evidence = config.get('executor_policy_operator_verification')
    need(isinstance(evidence, dict) and set(evidence) == {
        'purpose', 'receipt_sha256', 'executor_role_arn', 'inline_policy_name',
        'inline_sha256', 'managed_policy_arn', 'managed_sha256', 'managed_default_version',
        'managed_attachment_roles', 'managed_attachment_users', 'managed_attachment_groups',
        'executor_managed_policy_arns', 'executor_inline_policy_names', 'executor_permissions_boundary'},
        'Missing exact operator policy verification')
    expected = {key: EXPECTED[key] for key in (
        'executor_role_arn', 'inline_policy_name', 'inline_sha256', 'managed_policy_arn',
        'managed_sha256', 'managed_attachment_roles', 'managed_attachment_users', 'managed_attachment_groups')}
    expected.update(purpose='operator-verified-executor-policy-pair',
                    executor_managed_policy_arns=[GUARD],
                    executor_inline_policy_names=['FoundationStagedPolicy'],
                    executor_permissions_boundary=None)
    need(all(evidence.get(key) == value for key, value in expected.items()),
         'Operator verification differs from exact executor pair or attachment scope')
    need(re.fullmatch('[a-f0-9]{64}', evidence['receipt_sha256'] or '') is not None and
         evidence['receipt_sha256'] != '0'*64, 'Missing independently reviewed operator receipt digest')
    need(re.fullmatch('v[1-9][0-9]*', evidence['managed_default_version'] or '') is not None,
         'Missing observed managed policy default version')
    # This pins the separately reviewed operator observation. It is not an IAM read
    # and cannot prove that attachment/default-version state has not changed later.
    return pair_hash
