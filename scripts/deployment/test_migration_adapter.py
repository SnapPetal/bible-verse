"""First-migration provenance/rebind tests use only mocked APIs and local ZIP artifacts."""
import base64,copy,hashlib,io,json,subprocess,tempfile,unittest,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
import private_records as private
import bible_deploy as d
import migration_adapter as m
from test_java_release import load

def migration_plan():
 b=load('baseline');t=copy.deepcopy(b);receipts={}
 def ref(code):
  key='assets/'+code['S3Key'].split('/')[-1];raw=('fixture:'+key).encode();a={'bucket':d.BUCKET,'owner':d.ACCOUNT,'key':key,'version_id':'fixture-v1','bytes':len(raw),'sha256_base64':base64.b64encode(hashlib.sha256(raw).digest()).decode()};receipts[key]=a
  return {'S3Bucket':d.BUCKET,'S3Key':key,'S3ObjectVersion':a['version_id']}
 for r in t['Resources'].values():
  if r['Type']=='AWS::Lambda::Function':r['Properties']['Code']=ref(r['Properties']['Code'])
  if r['Type']=='AWS::Lambda::LayerVersion':r['Properties']['Content']=ref(r['Properties']['Content'])
  if r['Type']=='Custom::CDKBucketDeployment':
   r['Properties']['SourceObjectKeys']=[ref({'S3Key':key})['S3Key'] for key in r['Properties']['SourceObjectKeys']]
 raw=d.canonical(t);checksum=d.sha(raw)
 return {'kind':'first-migration','source_commit':'c7d06e4f1808f313408cc43b1addf4f75695e9d7','baseline':b,'template':t,'baseline_executor':m.OLD_EXECUTOR,'baseline_sha256':m.digest(b),'candidate_sha256':checksum,'capabilities':['CAPABILITY_NAMED_IAM'],'assets':list(receipts.values()),'asset_policy_sha256':m.digest({'Version':'2012-10-17','Statement':[]}),'template_asset':{'bucket':d.BUCKET,'owner':d.ACCOUNT,'key':'reviews/'+checksum+'.json','version_id':'template-v1','bytes':len(raw),'sha256_base64':base64.b64encode(hashlib.sha256(raw).digest()).decode()}}
class Cloud:
 def __init__(self,p):
  self.p=p;self.source=p['source_commit'];self.role=m.OLD_EXECUTOR;self.template=p['baseline'];self.status='UPDATE_COMPLETE';self.cs=None;self.mutations=[];self.artifacts={};self.zips={};self.private_records={};self.clock=datetime(2026,11,7,15,0,tzinfo=timezone.utc);self.run=201;self.event=None;self.calls=[]
 def now(self):return self.clock
 def github(self,path):
  if path.endswith('git/ref/heads/main'):return {'object':{'sha':self.source}}
  if '/actions/artifacts?' in path:return {'artifacts':list(self.artifacts.values())}
  if '/actions/artifacts/' in path:return copy.deepcopy(self.artifacts[int(path.rsplit('/',1)[1])])
  if '/actions/runs/' in path:
   return {'repository':{'full_name':m.REPO},'head_repository':{'full_name':m.REPO},'path':m.WORKFLOW,'head_sha':self.source,'head_branch':'main','event':'workflow_dispatch','actor':{'id':7},'triggering_actor':{'id':7},'status':'completed','conclusion':'success'}
  if '/deployments/' in path:
   rid=int(path.split('/deployments/')[1].split('/')[0])-1000
   if '/statuses?' in path:return [{'environment':'production','state':'success','log_url':f'https://github.com/{m.REPO}/actions/runs/{rid}/job/1'}]
   return {'environment':'production','sha':self.source,'performed_via_github_app':{'slug':'github-actions'}}
  raise AssertionError(path)
 def archive(self,ident):return self.zips[int(ident)]
 def private_record(self,ref):
  raw=self.private_records[(ref['key'],ref['version_id'])];return private.decode(ref,raw,{'VersionId':ref['version_id'],'ContentLength':len(raw),'ChecksumSHA256':ref['sha256_base64'],'ServerSideEncryption':'AES256'})
 def upload_model(self,record):
  names={'migration-intent':('bible-migration-intent-','operation.json'),'migration-response':('bible-migration-response-','response.json'),'migration-operator-evidence':('bible-migration-operator-','operator-evidence.json')};prefix,filename=names[record['kind']]
  stream=io.BytesIO()
  private_bytes=d.canonical(record);ref=private.reference(private_bytes,('private-'+str(len(self.artifacts)+1)).ljust(32,'x'));self.private_records[(ref['key'],ref['version_id'])]=private_bytes
  with zipfile.ZipFile(stream,'w') as z:z.writestr('record-ref.json',d.canonical(ref))
  ident=len(self.artifacts)+1;raw=stream.getvalue();hashed='sha256:'+hashlib.sha256(raw).hexdigest();self.zips[ident]=raw
  self.artifacts[ident]={'id':ident,'name':prefix+record['plan_sha256']+'-'+record['state'],'digest':hashed,'expired':False,'created_at':self.clock.isoformat(),'expires_at':(self.clock+timedelta(days=3)).isoformat(),'workflow_run':{'id':record['run_id'],'head_sha':self.source}}
  return ident,hashed
 def aws(self,service,operation,**kw):
  self.calls.append((service,operation))
  if service=='sts':return {'Account':d.ACCOUNT,'Arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeploy/bible-deploy-{self.run}'}
  if service=='s3api':
   if operation=='head-object':
    a=next(a for a in self.p['assets']+[self.p['template_asset']] if a['key']==kw['key']);return {'ServerSideEncryption':'AES256','ContentLength':a['bytes'],'ChecksumSHA256':a['sha256_base64'],'VersionId':a['version_id']}
   if operation=='get-bucket-policy':return {'Policy':json.dumps({'Version':'2012-10-17','Statement':[]})}
   if operation=='get-bucket-versioning':return {'Status':'Enabled'}
   if operation=='get-bucket-lifecycle-configuration':raise subprocess.CalledProcessError(1,'aws',stderr='(NoSuchLifecycleConfiguration)')
  if operation=='describe-stacks':return {'Stacks':[{'StackId':d.STACK,'StackStatus':self.status,'RoleARN':self.role}]}
  if operation=='get-template':return {'TemplateBody':copy.deepcopy(self.p['template'] if kw.get('change_set_name') else self.template)}
  if operation=='describe-change-set':
   if self.cs is None:raise subprocess.CalledProcessError(1,'aws',stderr='(ChangeSetNotFound)')
   return copy.deepcopy(self.cs)
  if operation=='create-change-set':
   changed=[(k,v) for k,v in self.p['template']['Resources'].items() if v!=self.p['baseline']['Resources'].get(k)]
   self.cs={'ChangeSetName':kw['change_set_name'],'ChangeSetId':f'arn:aws:cloudformation:{d.REGION}:{d.ACCOUNT}:changeSet/{kw["change_set_name"]}/id-123','StackId':d.STACK,'Description':kw['description'],'Capabilities':kw['capabilities'],'Status':'CREATE_COMPLETE','ExecutionStatus':'AVAILABLE','Changes':[{'ResourceChange':{'LogicalResourceId':k,'ResourceType':v['Type'],'Action':'Modify','Replacement':'True' if v['Type']=='AWS::Lambda::LayerVersion' else 'False'}} for k,v in changed]}
   self.event={'eventID':'event1','requestID':'request1','eventTime':self.clock.isoformat(),'eventName':'CreateChangeSet','eventSource':'cloudformation.amazonaws.com','awsRegion':d.REGION,'recipientAccountId':d.ACCOUNT,'userIdentity':{'arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeploy/bible-deploy-{self.run}','sessionContext':{'sessionIssuer':{'arn':m.ce.DEPLOY}}},'requestParameters':{'stackName':kw['stack_name'],'changeSetName':kw['change_set_name'],'changeSetType':kw['change_set_type'],'capabilities':kw['capabilities'],'roleARN':kw['role_arn'],'templateURL':kw['template_url'],'clientToken':kw['client_token'],'description':kw['description']},'responseElements':{'id':self.cs['ChangeSetId'],'stackId':d.STACK}}
   self.mutations.append(operation);return {'Id':self.cs['ChangeSetId'],'StackId':d.STACK}
  if operation=='describe-events':return {'OperationEvents':[]}
  if operation=='execute-change-set':self.mutations.append(operation);self.cs['ExecutionStatus']='EXECUTE_COMPLETE';self.template=self.p['template'];self.role=d.EXECUTOR;return {}
  raise AssertionError((service,operation,kw))
class Migration(unittest.TestCase):
 def setUp(self):self.p=migration_plan();self.cloud=Cloud(self.p);self.adapter=self.make(201)
 def make(self,run):
  self.cloud.run=run;return m.MigrationAdapter(self.cloud,[7],self.p['source_commit'],{'source_commit':self.p['source_commit'],'plan_sha256':m.digest(self.p)},run,operators=[7])
 def persist(self,record,adapter=None):
  adapter=adapter or self.adapter;record={**record,'deployment_id':record['run_id']+1000};ident,hashed=self.cloud.upload_model(record);return adapter.read_record(ident,hashed,record['kind'])
 def create(self,response=True):
  intent=self.persist(self.adapter.prepare_intent(self.p));result=self.adapter.submit(intent)
  if not response:return intent,None
  record={'kind':'migration-response','state':'create','source_commit':self.p['source_commit'],'plan_sha256':m.digest(self.p),'run_id':201,'intent_artifact_id':intent['_artifact_binding']['id'],'intent_artifact_digest':intent['_artifact_binding']['digest'],'request_sha256':intent['request_sha256'],'response':result}
  return intent,self.persist(record)
 def execute_stage(self,intent,response,operator=None,adapter=None):
  adapter=adapter or self.adapter
  record=adapter.execute_intent(intent,response,operator,self.cloud.cs['ChangeSetId'],m.digest(self.cloud.cs['Changes']));return self.persist(record,adapter)
 def test_old_executor_to_dedicated_postflight_without_changeset_rolearn(self):
  intent,response=self.create();self.assertNotIn('RoleARN',self.cloud.cs);self.assertEqual(self.cloud.role,m.OLD_EXECUTOR)
  execute=self.execute_stage(intent,response);self.adapter.submit(execute);result=self.adapter.postflight(execute)
  self.assertEqual(result['executor'],d.EXECUTOR);self.assertEqual(self.cloud.mutations,['create-change-set','execute-change-set']);self.assertFalse(any(x[0]=='cloudtrail' for x in self.cloud.calls))
 def test_missing_own_response_blocks_until_authenticated_operator_receipt(self):
  intent,_=self.create(response=False);restarted=self.make(202)
  with self.assertRaises(ValueError):self.execute_stage(intent,None,adapter=restarted)
  record={'kind':'migration-operator-evidence','state':'create','source_commit':self.p['source_commit'],'plan_sha256':m.digest(self.p),'run_id':301,'intent_artifact_id':intent['_artifact_binding']['id'],'intent_artifact_digest':intent['_artifact_binding']['digest'],'request_sha256':intent['request_sha256'],'event':self.cloud.event}
  operator=self.persist(record,restarted);execute=self.execute_stage(intent,None,operator,restarted);restarted.submit(execute);self.assertTrue(restarted.postflight(execute)['complete'])
 def test_own_response_identity_and_request_mismatches(self):
  intent,response=self.create()
  for path,value in [('run_id',999),('request_sha256','wrong'),('intent_artifact_id',999),('intent_artifact_digest','wrong'),('response.Id','wrong'),('response.StackId','wrong')]:
   bad=copy.deepcopy(response)
   if '.' in path:a,b=path.split('.');bad[a][b]=value
   else:bad[path]=value
   with self.subTest(path=path),self.assertRaises(ValueError):self.execute_stage(intent,bad)
  self.assertEqual(self.cloud.mutations,['create-change-set'])
 def test_wrong_stored_pre_executor_is_not_accepted_as_change_set_proof(self):
  intent,response=self.create()
  for wrong in [d.EXECUTOR,'arn:aws:iam::664759038511:role/Other']:
   self.cloud.role=wrong
   with self.subTest(wrong=wrong),self.assertRaises(ValueError):self.execute_stage(intent,response)
 def test_postflight_must_be_dedicated_and_exact(self):
  intent,response=self.create();execute=self.execute_stage(intent,response);self.adapter.submit(execute)
  for bad in [m.OLD_EXECUTOR,'other']:
   self.cloud.role=bad
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.adapter.postflight(execute)
  self.cloud.role=d.EXECUTOR;self.cloud.template=self.p['baseline']
  with self.assertRaises(ValueError):self.adapter.postflight(execute)
 def test_server_changes_mismatch_and_uncertain_outcomes_stop(self):
  intent,response=self.create();original=copy.deepcopy(self.cloud.cs)
  for key,value in [('StackId','other'),('Description','other'),('Capabilities',[]),('ChangeSetId',original['ChangeSetId']+'changed'),('ExecutionStatus','EXECUTE_COMPLETE'),('NextToken','more')]:
   self.cloud.cs={**copy.deepcopy(original),key:value}
   with self.subTest(key=key),self.assertRaises(ValueError):self.execute_stage(intent,response)
  self.cloud.cs=None
  with self.assertRaises(ValueError):self.adapter.execute_intent(intent,response,None,original['ChangeSetId'],m.digest(original['Changes']))
 def test_repeat_prepare_and_wrong_review_digest_rejected(self):
  intent,response=self.create()
  with self.assertRaises(ValueError):self.adapter.prepare_intent(self.p)
  with self.assertRaises(ValueError):self.adapter.execute_intent(intent,response,None,self.cloud.cs['ChangeSetId'],'0'*64)
 def test_operator_wrong_role_token_session_and_response_rejected(self):
  intent,_=self.create(response=False)
  for path,value in [('requestParameters.roleARN','other'),('requestParameters.clientToken','other'),('userIdentity.arn','other'),('responseElements.id','other')]:
   event=copy.deepcopy(self.cloud.event);first,second=path.split('.');event[first][second]=value
   operator={'kind':'migration-operator-evidence','intent_artifact_id':intent['_artifact_binding']['id'],'intent_artifact_digest':intent['_artifact_binding']['digest'],'request_sha256':intent['request_sha256'],'event':event}
   with self.subTest(path=path),self.assertRaises(ValueError):self.execute_stage(intent,None,operator)
 def test_direct_old_migration_entrypoints_cannot_mutate(self):
  with self.assertRaises(ValueError):d.prepare(None,None)
  with self.assertRaises(ValueError):d.execute(None,None)
if __name__=='__main__':unittest.main()
