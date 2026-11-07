import copy,json,tempfile,unittest
from pathlib import Path
import java_release as j
FIX=Path(__file__).parent/'fixtures/java-release'
def load(n):return json.loads((FIX/(n+'.json')).read_text())
def plan():return j.derive(load('baseline'),load('candidate'),load('assets'),load('review.FIXTURE'))
def observation(p,phase='baseline',aliases='old'):
 e={'account':j.ACCOUNT,'region':j.REGION,'caller_role':j.DEPLOY,'stack_id':j.STACK,'executor':j.EXECUTOR,'current_main':p['review']['source_commit'],'checked_out':p['review']['source_commit'],'stack_status':'UPDATE_COMPLETE','template':p[phase],'functions':{}}
 for fid,info in j.FUNCTIONS.items():
  arn=f'arn:aws:lambda:{j.REGION}:{j.ACCOUNT}:function:{info["name"]}';old_number=p['review']['old_versions'][fid]['version'];v=str(int(old_number)+1)
  old={'FunctionArn':arn+':'+old_number,'Version':old_number,'CodeSha256':p['review']['old_versions'][fid]['code_sha256'],'Role':p['review']['old_versions'][fid]['role_arn'],'State':'Active','SnapStart':{'OptimizationStatus':'On'}}
  new={**old,'FunctionArn':arn+':'+v,'Version':v,'CodeSha256':p['assets'][fid]['sha256_base64']}
  e['functions'][fid]={'old':old,'new':new,'new_physical_id':arn+':'+v,'new_logical_id':p['versions'][fid]['new_logical'],'old_logical_id':p['review']['old_versions'][fid]['logical_id'],'old_physical_id':p['review']['old_versions'][fid]['physical_id'],'old_mapping_source':'recorded-baseline' if phase=='phase2' else 'current-stack','alias':{'AliasArn':arn+':live','FunctionVersion':old_number if aliases=='old' else v}}
 return e

def asset(p,phase):
 digest=j.sha(j.canonical(p[f'phase{phase}']));return {'owner':j.ACCOUNT,'bucket':j.BUCKET,'key':f'reviews/{digest}.json','sha256':digest,'version_id':'fixture-template-v1','encryption':'AES256','bytes':len(j.canonical(p[f'phase{phase}'])),'sha256_base64':__import__('base64').b64encode(__import__('hashlib').sha256(j.canonical(p[f'phase{phase}'])).digest()).decode(),'checksum_type':'FULL_OBJECT'}
def changes(req,p,phase):
 allowed={fid:('Modify','AWS::Lambda::Function') for fid in j.FUNCTIONS} if phase==1 else {v['alias']:('Modify','AWS::Lambda::Alias') for v in j.FUNCTIONS.values()}
 if phase==1:allowed.update({v['new_logical']:('Add','AWS::Lambda::Version') for v in p['versions'].values()})
 else:allowed.update({v['old_logical']:('Remove','AWS::Lambda::Version') for v in p['versions'].values()})
 return {'ChangeSetName':req['ChangeSetName'],'Description':req['Description'],'ChangeSetId':f'arn:aws:cloudformation:{j.REGION}:{j.ACCOUNT}:changeSet/{req["ChangeSetName"]}/fixture-id','StackId':j.STACK,'ExecutorEvidence':{'source':'ImmutableOwnCreateResponse','role_arn':j.EXECUTOR,'change_set_arn':f'arn:aws:cloudformation:{j.REGION}:{j.ACCOUNT}:changeSet/{req["ChangeSetName"]}/fixture-id','response_artifact_id':1,'request_sha256':'fixture'},'Capabilities':p['review']['capabilities'],'Status':'CREATE_COMPLETE','ExecutionStatus':'AVAILABLE','Changes':[{'ResourceChange':{'LogicalResourceId':k,'Action':v[0],'ResourceType':v[1],'Replacement':'False','Scope':['Properties'],**({'PolicyAction':'Retain'} if v[0]=='Remove' else {})}} for k,v in allowed.items()]}
class Releases(unittest.TestCase):
 def setUp(self):self.p=plan();self.tmp=tempfile.TemporaryDirectory();self.op=j.Operation(Path(self.tmp.name)/'operation.json')
 def tearDown(self):self.tmp.cleanup()
 def stage(self,phase):
  e=observation(self.p,'baseline' if phase==1 else 'phase1');req=self.op.request(phase,'create',e,asset(self.p,phase));cs=changes(req,self.p,phase)
  self.op.reconcile_create(phase,cs,self.p[f'phase{phase}']);e['change_set']=cs;e['change_set_template']=self.p[f'phase{phase}'];execute=self.op.request(phase,'execute',e)
  self.assertEqual(execute['ChangeSetName'],cs['ChangeSetId']);return cs
 def complete_first(self):
  self.op.start(self.p,observation(self.p));cs=self.stage(1);e=observation(self.p,'phase1');e['change_set']={**cs,'ExecutionStatus':'EXECUTE_COMPLETE'};self.op.reconcile_execute(1,e)
 def test_exact_runtime_role_cannot_be_reapproved_as_other_role(self):
  for fid in j.FUNCTIONS:
   b=load('baseline');c=load('candidate');a=load('assets');r=load('review.FIXTURE')
   r['old_versions'][fid]['role_arn']='arn:aws:iam::664759038511:role/other'
   with self.assertRaises(ValueError):j.derive(b,c,a,r)
   r=load('review.FIXTURE')
   for t in [b,c]:t['Resources'][fid]['Properties']['Role']={'Fn::GetAtt':['OtherRole','Arn']}
   r['baseline_sha256']=j.sha(j.canonical(b));r['candidate_sha256']=j.sha(j.canonical(c))
   with self.assertRaises(ValueError):j.derive(b,c,a,r)
 def test_two_phases_and_no_old_version_deletion(self):
  self.complete_first();cs=self.stage(2);e=observation(self.p,'phase2','new');e['change_set']={**cs,'ExecutionStatus':'EXECUTE_COMPLETE'};self.op.reconcile_execute(2,e)
  self.assertEqual(json.loads(self.op.path.read_text())['state'],'complete')
  for v in self.p['versions'].values():self.assertNotIn(v['old_logical'],self.p['phase2']['Resources'])
  self.assertEqual(self.p['phase2'],load('candidate'))
 def test_code_only_derivation_rejects_every_other_resource_delta(self):
  c=load('candidate');b=load('baseline');a=load('assets')
  for key in c['Resources']:
   bad=copy.deepcopy(c);bad['Resources'][key].setdefault('Properties',{})['Unexpected']='change'
   review=load('review.FIXTURE');review['candidate_sha256']=j.sha(j.canonical(bad))
   with self.subTest(key=key),self.assertRaises(ValueError):j.derive(b,bad,a,review)
 def test_external_review_fingerprints_required(self):
  for key in ['baseline_sha256','candidate_sha256','assets_sha256','phase1_sha256','phase2_sha256']:
   review=load('review.FIXTURE');review[key]='0'*64
   with self.subTest(key=key),self.assertRaises(ValueError):j.derive(load('baseline'),load('candidate'),load('assets'),review)
 def test_identity_main_stack_and_alias_negatives(self):
  for k,v in [('account','other'),('region','us-west-2'),('caller_role','shared'),('stack_id','other'),('executor','shared'),('current_main','a'*40),('stack_status','UPDATE_IN_PROGRESS')]:
   e=observation(self.p);e[k]=v
   with self.subTest(k=k),self.assertRaises(ValueError):self.op.start(self.p,e)
  e=observation(self.p);next(iter(e['functions'].values()))['alias']['FunctionVersion']='999'
  with self.assertRaises(ValueError):self.op.start(self.p,e)
 def test_no_phase2_before_readiness(self):
  self.op.start(self.p,observation(self.p));cs=self.stage(1)
  with self.assertRaises(ValueError):self.op.request(2,'create',observation(self.p,'phase1'),asset(self.p,2))
  for field,value in [('State','Pending'),('CodeSha256','wrong'),('Version','999'),('Role','wrong'),('FunctionArn','wrong'),('SnapStart',{'OptimizationStatus':'Off'})]:
   e=observation(self.p,'phase1');e['change_set']={**cs,'ExecutionStatus':'EXECUTE_COMPLETE'};next(iter(e['functions'].values()))['new'][field]=value
   with self.subTest(field=field),self.assertRaises(ValueError):self.op.reconcile_execute(1,e)
  self.assertEqual(json.loads(self.op.path.read_text())['state'],'phase1_execute_intent')
 def test_partial_and_wrong_change_set_receipts(self):
  self.op.start(self.p,observation(self.p));req=self.op.request(1,'create',observation(self.p),asset(self.p,1));cs=changes(req,self.p,1)
  for k,v in [('RoleARN','wrong'),('StackId','wrong'),('Status','CREATE_IN_PROGRESS'),('NextToken','more'),('Description','wrong'),('Changes',cs['Changes'][:1])]:
   bad=copy.deepcopy(cs);bad[k]=v
   with self.subTest(k=k),self.assertRaises(ValueError):self.op.reconcile_create(1,bad,self.p['phase1'])
 def test_crash_replay_create_and_execute_no_second_request(self):
  self.op.start(self.p,observation(self.p));req=self.op.request(1,'create',observation(self.p),asset(self.p,1));reopened=j.Operation(self.op.path)
  with self.assertRaises(ValueError):reopened.request(1,'create',observation(self.p),asset(self.p,1))
  cs=changes(req,self.p,1);reopened.reconcile_create(1,cs,self.p['phase1']);e=observation(self.p);e.update(change_set=cs,change_set_template=self.p['phase1']);reopened.request(1,'execute',e)
  with self.assertRaises(ValueError):j.Operation(self.op.path).request(1,'execute',e)
 def test_wrong_template_and_artifact_receipts(self):
  self.op.start(self.p,observation(self.p))
  for field,value in [('owner','wrong'),('key','templates/wrong.json'),('version_id','null'),('sha256','wrong')]:
   a=asset(self.p,1);a[field]=value
   with self.subTest(field=field),self.assertRaises(ValueError):self.op.request(1,'create',observation(self.p),a)
 def test_phase2_cannot_accept_partial_alias_movement(self):
  self.complete_first();cs=self.stage(2);e=observation(self.p,'phase2','new');e['change_set']={**cs,'ExecutionStatus':'EXECUTE_COMPLETE'};fid=next(iter(j.FUNCTIONS));e['functions'][fid]['alias']['FunctionVersion']=self.p['review']['old_versions'][fid]['version']
  with self.assertRaises(ValueError):self.op.reconcile_execute(2,e)
  self.assertEqual(json.loads(self.op.path.read_text())['state'],'phase2_execute_intent')
 def test_changed_verified_version_rejected(self):
  self.complete_first();e=observation(self.p,'phase1');x=next(iter(e['functions'].values()));x['new']['Version']='999';x['new']['FunctionArn']=x['new']['FunctionArn'].rsplit(':',1)[0]+':999';x['new_physical_id']=x['new']['FunctionArn']
  with self.assertRaises(ValueError):self.op.request(2,'create',e,asset(self.p,2))
 def test_two_successive_releases_return_to_canonical_source(self):
  self.test_two_phases_and_no_old_version_deletion()
  first_final=copy.deepcopy(self.p['phase2'])
  second=lambda n:json.loads((FIX/'second-release'/(n+'.json')).read_text())
  self.p=j.derive(second('baseline'),second('candidate'),second('assets'),second('review.FIXTURE'))
  self.assertEqual(self.p['baseline'],first_final)
  self.assertEqual({v['old_version'] for v in self.p['versions'].values()},{'33','37'})
  self.op=j.Operation(Path(self.tmp.name)/'second-operation.json');self.complete_first();cs=self.stage(2)
  e=observation(self.p,'phase2','new');e['change_set']={**cs,'ExecutionStatus':'EXECUTE_COMPLETE'};self.op.reconcile_execute(2,e)
  self.assertEqual(json.loads(self.op.path.read_text())['state'],'complete')
  self.assertEqual(self.p['phase2'],second('candidate'))
  for v in self.p['versions'].values():self.assertNotIn(v['old_logical'],self.p['phase2']['Resources'])
 def test_retained_removal_requires_actual_policy_action(self):
  self.complete_first();req=self.op.request(2,'create',observation(self.p,'phase1'),asset(self.p,2));cs=changes(req,self.p,2)
  for action in ['Delete','ReplaceAndDelete',None]:
   bad=copy.deepcopy(cs)
   for change in bad['Changes']:
    if change['ResourceChange']['Action']=='Remove':change['ResourceChange']['PolicyAction']=action
   with self.subTest(action=action),self.assertRaises(ValueError):self.op.reconcile_create(2,bad,self.p['phase2'])
 def test_old_cfn_mapping_must_match_review(self):
  for key in ['old_logical_id','old_physical_id','old_mapping_source']:
   e=observation(self.p);next(iter(e['functions'].values()))[key]='wrong'
   with self.subTest(key=key),self.assertRaises(ValueError):self.op.start(self.p,e)
if __name__=='__main__':unittest.main()
