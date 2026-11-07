import copy, importlib.util, json, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import bible_deploy as d
class Guards(unittest.TestCase):
 def setUp(self):
  self.t={'Resources':{d.AUTO:{'Type':'Custom::S3AutoDeleteObjects','Properties':{'ServiceToken':'untouched'},'DeletionPolicy':'Retain','UpdateReplacePolicy':'Retain'}}}
  self.c={'candidate_sha256':d.sha(d.canonical(self.t))}
  self.cs='arn:aws:cloudformation:us-east-1:664759038511:changeSet/bible-isolated-update-abc/123-456'
  self.x={'ChangeSetId':self.cs,'StackId':d.STACK,'ExecutorEvidence':{'source':'ImmutableOwnCreateResponse','role_arn':d.EXECUTOR,'change_set_arn':self.cs,'response_artifact_id':1,'request_sha256':'fixture'},'Status':'CREATE_COMPLETE','ExecutionStatus':'AVAILABLE','Changes':[{'ResourceChange':{'LogicalResourceId':'About','ResourceType':'AWS::Lambda::Function','Action':'Modify','Replacement':'False'}}]}
 def test_locked_without_aws(self):
  with patch.object(d,'aws',side_effect=AssertionError('must not call AWS')):
   with self.assertRaises(ValueError): d.active()
 def test_two_app_gate_without_false_boundary_or_recovery_claim(self):
  c={'enabled':True,'java_release_enabled':True,'migration_adapter_enabled':False,'mode':'two-app-java','baseline_executor':d.EXECUTOR,'retention_applied':True,'entry_replaced':True,'executor_permissions_approved':True,'asset_policy_verified':True,'asset_policy_sha256':'a'*64}
  with patch.object(d,'load',return_value=c),patch.object(d,'aws',side_effect=AssertionError('offline gate')):
   self.assertEqual(d.java_active(),c)
   with self.assertRaises(ValueError):d.active()
  for key,value in [('enabled',False),('java_release_enabled',False),('migration_adapter_enabled',True),('mode','steady-state'),('baseline_executor','shared'),('retention_applied',False),('asset_policy_sha256','bad')]:
   changed={**c,key:value}
   with self.subTest(key=key),patch.object(d,'load',return_value=changed),self.assertRaises(ValueError):d.java_active()
 def test_legacy_gate_still_requires_boundary_and_recovery(self):
  c={'enabled':True,'mode':'one-time-byte-preserving','retention_applied':True,'entry_replaced':True,'executor_permissions_approved':True,'asset_policy_verified':True,'baseline_sha256':'a'*64,'candidate_sha256':'b'*64,'asset_policy_sha256':'c'*64}
  with patch.object(d,'load',return_value=c),self.assertRaises(ValueError):d.active()
 def test_exact_change_set(self):
  self.assertEqual(len(d.check_changes(self.x,self.c,self.t,self.cs)),64)
 def test_identity_and_status_negatives(self):
  for k,v in [('StackId','other'),('RoleARN','shared'),('Status','FAILED'),('ExecutionStatus','EXECUTE_COMPLETE'),('ChangeSetId',self.cs.replace('bible-isolated','faith-isolated')),('IncludeNestedStacks',True),('ParentChangeSetId','nested')]:
   with self.subTest(k=k):
    x=copy.deepcopy(self.x);x[k]=v
    with self.assertRaises(ValueError): d.check_changes(x,self.c,self.t,self.cs)
 def test_resource_negatives(self):
  for k,v in [('LogicalResourceId',d.AUTO),('ResourceType','AWS::IAM::Role'),('Replacement','Conditional'),('Replacement','True'),('Action','Remove')]:
   with self.subTest(k=k):
    x=copy.deepcopy(self.x);x['Changes'][0]['ResourceChange'][k]=v
    with self.assertRaises(ValueError): d.check_changes(x,self.c,self.t,self.cs)
 def test_template_digest(self):
  with self.assertRaises(ValueError): d.check_changes(self.x,self.c,{'Resources':{}},self.cs)
 def test_autodelete_properties_immutable(self):
  t=copy.deepcopy(self.t); t['Resources'][d.AUTO]['Properties']['Anything']='changed'
  c={'candidate_sha256':d.sha(d.canonical(t))}
  with self.assertRaises(ValueError): d.validate_candidate(t,self.t,c)
 def test_retention_required(self):
  b=copy.deepcopy(self.t); b['Resources']['OldVersion']={'Type':'AWS::Lambda::Version'}
  t=copy.deepcopy(b); c={'candidate_sha256':d.sha(d.canonical(t))}
  with self.assertRaises(ValueError): d.validate_candidate(t,b,c)
 def test_alias_shift_not_implicitly_approved(self):
  b=copy.deepcopy(self.t); b['Resources']['Live']={'Type':'AWS::Lambda::Alias','Properties':{'FunctionVersion':'32'}}
  t=copy.deepcopy(b); t['Resources']['Live']['Properties']['FunctionVersion']='33'; c={'candidate_sha256':d.sha(d.canonical(t))}
  with self.assertRaises(ValueError): d.validate_candidate(t,b,c)
 def test_conditional_publish_one_put(self):
  data=b'exact verified bytes'; a={'key':'assets/'+'a'*64+'.zip','bytes':len(data),'sha256_base64':d.base64.b64encode(d.hashlib.sha256(data).digest()).decode()}
  h={'ServerSideEncryption':'AES256','ContentLength':a['bytes'],'ChecksumSHA256':a['sha256_base64'],'VersionId':'v1'}
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'asset';p.write_bytes(data)
   with patch.object(d,'aws',side_effect=[{'VersionId':'v1'},h]) as mock:
    self.assertEqual(d.publish_one(p,a)['version_id'],'v1')
    self.assertEqual(mock.call_count,2); self.assertEqual(mock.call_args_list[0].kwargs['if_none_match'],'*')
   with patch.object(d,'aws',side_effect=subprocess.CalledProcessError(1,'aws',stderr='PreconditionFailed')) as mock:
    with self.assertRaises(subprocess.CalledProcessError): d.publish_one(p,a)
    self.assertEqual(mock.call_count,1)
 def test_changed_payload_never_sent(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'asset';p.write_bytes(b'wrong')
   with patch.object(d,'aws') as mock:
    with self.assertRaises(ValueError): d.publish_one(p,{'key':'assets/'+'a'*64+'.zip','bytes':9,'sha256_base64':'bad'})
    mock.assert_not_called()
 def test_wrong_encryption_and_version_rejected(self):
  a={'bytes':1,'sha256_base64':'hash','key':'key'}
  for h in [{'ContentLength':1,'ChecksumSHA256':'hash','ServerSideEncryption':'aws:kms','VersionId':'v1'},{'ContentLength':1,'ChecksumSHA256':'hash','ServerSideEncryption':'AES256','VersionId':'null'}]:
   with self.assertRaises(ValueError): d.object_receipt(h,a)
 def test_pagination_rejected(self):
  with patch.object(d,'aws',return_value={'NextToken':'more'}):
   with self.assertRaises(ValueError):d.describe(self.cs)
if __name__=='__main__':unittest.main()

class RevisedGuards(unittest.TestCase):
 def test_actor_exact_role_and_account(self):
  for value in [{'Account':d.ACCOUNT,'Arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeploy/run'},{'Account':'wrong','Arn':'wrong'},{'Account':d.ACCOUNT,'Arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeployOther/run'}]:
   with patch.object(d,'aws',return_value=value):
    if value['Arn'].endswith('/BibleVerseCdkDeploy/run'):self.assertEqual(d.actor('BibleVerseCdkDeploy'),value)
    else:
     with self.assertRaises(ValueError):d.actor('BibleVerseCdkDeploy')
 def test_exclusive_intent_is_not_overwritten(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'intent.json';d.exclusive_intent(p,{'token':'first'})
   with self.assertRaises(FileExistsError):d.exclusive_intent(p,{'token':'second'})
   self.assertEqual(d.load(p),{'token':'first'})
 def test_offline_verify_accepts_both_activation_states_without_aws(self):
  for enabled in [True,False]:
   with patch('sys.argv',['bible_deploy.py','verify']),patch.object(d,'load',return_value={'enabled':enabled}),patch.object(d,'aws') as mock:
    d.main();mock.assert_not_called()
 def test_auth_verification_forces_dedicated_baseline(self):
  with tempfile.TemporaryDirectory() as td,patch.object(d,'active',return_value={'mode':'one-time-byte-preserving'}),patch.object(d,'source_gate'),patch.object(d,'actor',return_value={'Account':d.ACCOUNT}),patch.object(d,'baseline',return_value={}) as baseline:
   d.verify_auth(Path(td));self.assertEqual(baseline.call_args.args[0]['mode'],'steady-state')
 def test_prefix_and_standard_storage(self):
  data=b'test';a={'key':'assets/'+'a'*64+'.jar','bytes':4,'sha256_base64':d.base64.b64encode(d.hashlib.sha256(data).digest()).decode()}
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'asset';p.write_bytes(data)
   h={'ServerSideEncryption':'AES256','ContentLength':4,'ChecksumSHA256':a['sha256_base64'],'VersionId':'v1'}
   with patch.object(d,'aws',side_effect=[{'VersionId':'v1'},h]) as mock:
    d.publish_one(p,a);self.assertEqual(mock.call_args_list[0].kwargs['storage_class'],'STANDARD')
   a['key']='a'*64+'.jar'
   with patch.object(d,'aws') as mock:
    with self.assertRaises(ValueError):d.publish_one(p,a)
    mock.assert_not_called()
