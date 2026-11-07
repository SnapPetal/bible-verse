"""Mocked transport E2E only: no AWS/GitHub call or upload is performed."""
import base64,copy,hashlib,io,json,subprocess,tempfile,unittest,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
import private_records as private
import bible_deploy as d
import java_release as j
import java_adapter as a
from test_java_release import plan,load,observation,asset,changes
class Cloud:
 def __init__(self,p):
  self.p=p;self.live='baseline';self.sets={};self.artifacts={};self.zips={};self.private_records={};self.mutations=[];self.clock=datetime(2026,11,7,14,0,tzinfo=timezone.utc);self.source=p['review']['source_commit'];self.run=201;self.status='UPDATE_COMPLETE';self.bad=None;self.lose=None;self.events=[];self.calls=[]
  self.controls={'asset_policy_sha256':a.digest({'Version':'2012-10-17','Statement':[]})}
  self.receipts={f'phase{i}':asset(p,i) for i in [1,2]}
 def now(self):return self.clock
 def github(self,path):
  if path.endswith('git/ref/heads/main'):return {'object':{'sha':self.source}}
  if '/actions/artifacts?per_page' in path:return {'artifacts':list(self.artifacts.values())}
  if '/actions/artifacts/' in path:return copy.deepcopy(self.artifacts[int(path.rsplit('/',1)[1])])
  if '/actions/runs/' in path:
   rid=int(path.rsplit('/',1)[1]);return {'repository':{'full_name':a.REPO},'head_repository':{'full_name':a.REPO},'path':a.WORKFLOW,'head_sha':self.source,'head_branch':'main','event':'workflow_dispatch','actor':{'id':7},'triggering_actor':{'id':7},'status':'completed' if rid in [101,301] else 'in_progress','conclusion':'success' if rid in [101,301] else None}
  if '/deployments/' in path:
   did=int(path.split('/deployments/')[1].split('/')[0]);rid=did-1000
   if path.endswith('/statuses?per_page=100'):return [{'environment':'production','state':'success' if rid in [101,301] else 'in_progress','log_url':f'https://github.com/{a.REPO}/actions/runs/{rid}/job/1'}]
   return {'environment':'production','sha':self.source,'performed_via_github_app':{'slug':'github-actions'}}
  raise AssertionError(path)
 def archive(self,i):return self.zips[int(i)]
 def private_record(self,ref):
  raw=self.private_records[(ref['key'],ref['version_id'])];return private.decode(ref,raw,{'VersionId':ref['version_id'],'ContentLength':len(raw),'ChecksumSHA256':ref['sha256_base64'],'ServerSideEncryption':'AES256'})
 def upload_model(self,value):
  kind=value['kind']
  if kind=='java-review':name='bible-java-review-'+value['plan_sha256'];filename='review.json'
  elif kind=='java-intent':name='bible-java-intent-'+value['plan_sha256']+'-'+value['operation']['state'];filename='operation.json'
  else:name=('bible-java-response-' if kind=='java-response' else 'bible-java-operator-')+value['plan_sha256']+'-'+value['intent_state'];filename='response.json' if kind=='java-response' else 'operator-evidence.json'
  buf=io.BytesIO()
  private_bytes=d.canonical(value);ref=private.reference(private_bytes,('private-'+str(len(self.artifacts)+1)).ljust(32,'x'));self.private_records[(ref['key'],ref['version_id'])]=private_bytes
  with zipfile.ZipFile(buf,'w') as z:z.writestr('record-ref.json',d.canonical(ref))
  raw=buf.getvalue();ident=len(self.artifacts)+1;digest='sha256:'+hashlib.sha256(raw).hexdigest();self.zips[ident]=raw
  self.artifacts[ident]={'id':ident,'name':name,'digest':digest,'expired':False,'created_at':self.clock.isoformat(),'expires_at':(self.clock+timedelta(days=3)).isoformat(),'workflow_run':{'id':value['run_id'],'head_sha':self.source}}
  return str(ident),digest
 def config(self,name,qualifier):
  fid=next(fid for fid,v in j.FUNCTIONS.items() if v['name']==name);e=observation(self.p,'phase1')['functions'][fid];old=self.p['review']['old_versions'][fid]['version'];result=copy.deepcopy(e['old'] if qualifier==old else e['new'])
  result['SnapStart']['ApplyOn']='PublishedVersions'
  result.update(Runtime='java25',Handler='org.springframework.cloud.function.adapter.aws.FunctionInvoker',MemorySize=1024,Timeout=30,Environment={'Variables':{'fixture':'private-not-persisted'}})
  if qualifier!=old and self.bad:
   if self.bad=='wrong_version':result['Version']='999'
   if self.bad=='wrong_hash':result['CodeSha256']='wrong'
   if self.bad=='not_ready':result['SnapStart']['OptimizationStatus']='Off'
   if self.bad=='config_drift':result['MemorySize']=2048
  return result
 def aws(self,service,operation,**kw):
  self.calls.append((service,operation))
  if service=='cloudtrail':return {'Events':[{'CloudTrailEvent':json.dumps(x)} for x in self.events]}
  if service=='sts':return {'Account':d.ACCOUNT,'Arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeploy/bible-java-{self.run}'}
  if service=='s3api':
   if operation=='get-bucket-policy':return {'Policy':json.dumps({'Version':'2012-10-17','Statement':[]})}
   if operation=='get-bucket-versioning':return {'Status':'Enabled'}
   if operation=='get-bucket-lifecycle-configuration':raise subprocess.CalledProcessError(1,'aws',stderr='(NoSuchLifecycleConfiguration)')
   if operation=='head-object':
    receipt=next(x for x in list(self.p['assets'].values())+list(self.receipts.values()) if x['key']==kw['key'])
    return {'ServerSideEncryption':'AES256','ContentLength':receipt['bytes'],'ChecksumSHA256':receipt['sha256_base64'],'ChecksumType':'FULL_OBJECT','VersionId':receipt['version_id']}
  if service=='lambda':
   if operation=='get-function-configuration':
    if 'qualifier' in kw:return self.config(kw['function_name'],kw['qualifier'])
    fid=next(fid for fid,v in j.FUNCTIONS.items() if v['name']==kw['function_name']);old=self.p['review']['old_versions'][fid]['version'];result=self.config(kw['function_name'],old if self.live=='baseline' else str(int(old)+1));result.update(FunctionArn=f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{kw["function_name"]}',Version='$LATEST',LastUpdateStatus='Successful')
    if self.bad=='latest_drift':result['MemorySize']=2048
    if self.bad=='latest_hash':result['CodeSha256']='wrong'
    return result
   if operation=='get-alias':
    e=observation(self.p,'phase2' if self.live=='phase2' else 'baseline','new' if self.live=='phase2' else 'old');fid=next(fid for fid,v in j.FUNCTIONS.items() if v['name']==kw['function_name']);return e['functions'][fid]['alias']
  if service=='cloudformation':
   if operation=='describe-events':return {'OperationEvents':[]}
   if operation=='describe-stacks':return {'Stacks':[{'StackId':d.STACK,'RoleARN':d.EXECUTOR,'StackStatus':self.status}]}
   if operation=='get-template':
    phase=self.live
    if kw.get('change_set_name'):phase=self.lookup(kw['change_set_name'])['_phase']
    return {'TemplateBody':copy.deepcopy(self.p[phase])}
   if operation=='describe-stack-resource':
    logical=kw['logical_resource_id'];fid=next(fid for fid,v in self.p['versions'].items() if logical in [v['old_logical'],v['new_logical']]);old=logical==self.p['versions'][fid]['old_logical'];number=self.p['review']['old_versions'][fid]['version'];number=number if old else str(int(number)+1)
    return {'StackResourceDetail':{'LogicalResourceId':logical,'ResourceType':'AWS::Lambda::Version','PhysicalResourceId':f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{j.FUNCTIONS[fid]["name"]}:{number}'}}
   if operation=='describe-change-set':return {k:copy.deepcopy(v) for k,v in self.lookup(kw['change_set_name']).items() if k!='_phase'}
   if operation=='create-change-set':
    phase=1 if kw['description'].endswith('phase1') else 2
    req={'ChangeSetName':kw['change_set_name'],'Description':kw['description']};cs=changes(req,self.p,phase);cs.pop('RoleARN',None);cs.pop('ExecutorEvidence',None);cs['_phase']=f'phase{phase}';self.sets[req['ChangeSetName']]=cs;self.mutations.append(operation)
    self.events.append({'eventID':'event-'+str(len(self.events)+1),'requestID':'request-'+str(len(self.events)+1),'eventTime':self.clock.isoformat(),'eventName':'CreateChangeSet','eventSource':'cloudformation.amazonaws.com','awsRegion':d.REGION,'recipientAccountId':d.ACCOUNT,'userIdentity':{'arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeploy/bible-java-{self.run}','sessionContext':{'sessionIssuer':{'arn':j.DEPLOY}}},'requestParameters':{'stackName':kw['stack_name'],'changeSetName':kw['change_set_name'],'changeSetType':'UPDATE','capabilities':kw['capabilities'],'roleARN':kw['role_arn'],'templateURL':kw['template_url'],'clientToken':kw['client_token'],'description':kw['description']},'responseElements':{'id':cs['ChangeSetId'],'stackId':d.STACK}})
    if self.lose==operation:self.lose=None;raise TimeoutError('Response lost after accepted create')
    return {'Id':cs['ChangeSetId'],'StackId':d.STACK}
   if operation=='execute-change-set':
    cs=self.lookup(kw['change_set_name']);self.live=cs['_phase'];cs['ExecutionStatus']='EXECUTE_COMPLETE';self.mutations.append(operation)
    if self.lose==operation:self.lose=None;raise TimeoutError('Response lost after accepted execute')
    return {}
  raise AssertionError((service,operation,kw))
 def lookup(self,name):
  for cs in self.sets.values():
   if name in [cs['ChangeSetName'],cs['ChangeSetId']]:return cs
  raise subprocess.CalledProcessError(1,'aws',stderr='(ChangeSetNotFound)')

class Integrated(unittest.TestCase):
 def setUp(self):
  self.p=plan();self.controls={'asset_policy_sha256':a.digest({'Version':'2012-10-17','Statement':[]})};self.p['review']['controls_sha256']=a.digest(self.controls)
  self.cloud=Cloud(self.p);self.tmp=tempfile.TemporaryDirectory();self.adapter=self.make_adapter(201);self.index=0;self.responses={}
  self.review={'kind':'java-review','plan':self.p,'candidate':load('candidate'),'controls':self.controls,'template_receipts':self.cloud.receipts,'plan_sha256':a.digest(self.p),'source_commit':self.cloud.source,'run_id':101,'deployment_id':1101,'expires_at':(self.cloud.now()+timedelta(days=3)).isoformat()}
  self.rid,self.rhash=self.cloud.upload_model(self.review);self.review=self.adapter.read_artifact(self.rid,self.rhash,'java-review')
 def tearDown(self):self.tmp.cleanup()
 def make_adapter(self,run):
  self.cloud.run=run;return a.Adapter(self.cloud,[7],self.cloud.source,a.pinset(self.p),run,operator_actor_ids=[7])
 def stage(self,resume=None,adapter=None,operator=None):
  adapter=adapter or self.adapter;self.index+=1
  op=adapter.next_intent(self.review,Path(self.tmp.name)/str(self.index)/'operation.json',self.cloud.receipts,resume,self.responses.get(resume['_artifact_binding']['id']) if resume else None,operator)
  if op is None:return None
  op.setdefault('intent_origins',{})[op['state']]={'run_id':adapter.run_id,'created_at':self.cloud.now().isoformat()}
  value={'kind':'java-intent','source_commit':self.cloud.source,'plan_sha256':a.digest(self.p),'run_id':adapter.run_id,'run_attempt':1,'deployment_id':adapter.run_id+1000,'operation':op,'request_sha256':a.digest(op['history'][-1]['request']),'template_receipts':self.cloud.receipts,'controls':self.controls}
  ident,hashed=self.cloud.upload_model(value);return adapter.read_artifact(ident,hashed,'java-intent')
 def submit(self,checkpoint,adapter=None):
  adapter=adapter or self.adapter;result=adapter.submit(checkpoint)
  value={'kind':'java-response','source_commit':self.cloud.source,'plan_sha256':a.digest(self.p),'run_id':adapter.run_id,'deployment_id':adapter.run_id+1000,'intent_artifact_id':checkpoint['_artifact_binding']['id'],'intent_artifact_digest':checkpoint['_artifact_binding']['digest'],'intent_state':checkpoint['operation']['state'],'request_sha256':checkpoint['request_sha256'],'response':result}
  ident,hashed=self.cloud.upload_model(value);self.responses[checkpoint['_artifact_binding']['id']]=adapter.read_artifact(ident,hashed,'java-response');return result
 def operator_receipt(self,checkpoint,event=None):
  value={'kind':'java-operator-evidence','source_commit':self.cloud.source,'plan_sha256':a.digest(self.p),'run_id':301,'deployment_id':1301,'intent_artifact_id':checkpoint['_artifact_binding']['id'],'intent_artifact_digest':checkpoint['_artifact_binding']['digest'],'intent_state':checkpoint['operation']['state'],'request_sha256':checkpoint['request_sha256'],'event':copy.deepcopy(event if event is not None else self.cloud.events[-1])}
  ident,hashed=self.cloud.upload_model(value);return self.adapter.read_artifact(ident,hashed,'java-operator-evidence')
 def test_end_to_end_four_mutations_with_persisted_verified_intents(self):
  checkpoint=None
  for _ in range(4):checkpoint=self.stage(checkpoint);self.submit(checkpoint)
  self.assertIsNone(self.stage(checkpoint));self.assertEqual(self.cloud.mutations,['create-change-set','execute-change-set']*2);self.assertEqual(self.cloud.live,'phase2');self.assertFalse(any(service=='cloudtrail' for service,operation in self.cloud.calls))
  self.assertNotIn('Environment',checkpoint['operation']['baseline_runtime'][next(iter(j.FUNCTIONS))]['old'])
 def test_validation_gate_pagination_and_exact_scope(self):
  c=self.stage();self.submit(c);c=self.stage(c);original=self.cloud.aws;seen=[]
  def paged(service,operation,**kw):
   if operation!='describe-events':return original(service,operation,**kw)
   seen.append(kw);return {'OperationEvents':[],**({'NextToken':'second'} if len(seen)==1 else {})}
  self.cloud.aws=paged;self.submit(c)
  self.assertEqual(len(seen),2);self.assertEqual(seen[1]['next_token'],'second')
  self.assertTrue(all(x['stack_name']==d.STACK and ':changeSet/' in x['change_set_name'] for x in seen))
 def test_validation_findings_never_execute_or_leak(self):
  c=self.stage();self.submit(c);c=self.stage(c);original=self.cloud.aws
  cases=[{}, {'OperationEvents':[{'EventType':'PROGRESS_EVENT','StackId':'foreign'}]}, {'OperationEvents':[],'NextToken':'repeat'}]
  for status in ['FAILED','SKIPPED']:
   for mode in ['FAIL','WARN']:cases.append({'OperationEvents':[{'EventType':'VALIDATION_ERROR','ValidationStatus':status,'ValidationFailureMode':mode,'ValidationStatusReason':'PRIVATE_SECRET','ResourceProperties':'PRIVATE_SECRET'}]})
  for kind in ['HOOK_INVOCATION_ERROR','PROVISIONING_ERROR','UNKNOWN']:cases.append({'OperationEvents':[{'EventType':kind}]})
  for result in cases:
   def blocked(service,operation,**kw):
    return copy.deepcopy(result) if operation=='describe-events' else original(service,operation,**kw)
   self.cloud.aws=blocked
   with self.subTest(result=result),self.assertRaises(ValueError) as error:self.adapter.submit(c)
   self.assertNotIn('PRIVATE_SECRET',str(error.exception));self.assertEqual(self.cloud.mutations,['create-change-set'])
 def test_validation_read_failure_never_executes(self):
  c=self.stage();self.submit(c);c=self.stage(c);original=self.cloud.aws
  def denied(service,operation,**kw):
   if operation=='describe-events':raise RuntimeError('unavailable')
   return original(service,operation,**kw)
  self.cloud.aws=denied
  with self.assertRaises(RuntimeError):self.adapter.submit(c)
  self.assertEqual(self.cloud.mutations,['create-change-set'])
 def test_latest_drift_blocks_before_any_mutation(self):
  for bad in ['latest_drift','latest_hash']:
   self.cloud.bad=bad
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.stage()
   self.assertEqual(self.cloud.mutations,[])
 def test_no_submit_before_uploaded_artifact(self):
  with self.assertRaises(KeyError):self.adapter.read_artifact('999','sha256:'+'0'*64,'java-intent')
  self.assertFalse(self.cloud.mutations)
 def test_expired_tampered_and_wrong_provenance_artifacts(self):
  saved=copy.deepcopy(self.cloud.artifacts[int(self.rid)])
  for key,value in [('expired',True),('digest','sha256:'+'0'*64),('name','other'),('expires_at',(self.cloud.now()-timedelta(seconds=1)).isoformat())]:
   self.cloud.artifacts[int(self.rid)]={**saved,key:value}
   with self.subTest(key=key),self.assertRaises(ValueError):self.adapter.read_artifact(self.rid,self.rhash,'java-review')
  self.cloud.artifacts[int(self.rid)]=saved;self.cloud.zips[int(self.rid)]+=b'tampered'
  with self.assertRaises(ValueError):self.adapter.read_artifact(self.rid,self.rhash,'java-review')
 def test_crash_after_upload_before_request_stops_without_reissue(self):
  checkpoint=self.stage();restarted=self.make_adapter(202)
  with self.assertRaises(ValueError):self.stage(checkpoint,restarted)
  with self.assertRaises(ValueError):restarted.submit(checkpoint)
  self.assertEqual(self.cloud.mutations,[])
 def test_create_response_loss_reconciles_not_recreates(self):
  checkpoint=self.stage();self.cloud.lose='create-change-set'
  with self.assertRaises(TimeoutError):self.submit(checkpoint)
  restarted=self.make_adapter(202)
  with self.assertRaises(ValueError):self.stage(checkpoint,restarted)
  next_checkpoint=self.stage(checkpoint,restarted,self.operator_receipt(checkpoint));self.submit(next_checkpoint,restarted)
  self.assertEqual(self.cloud.mutations,['create-change-set','execute-change-set'])
 def test_execute_response_loss_reconciles_to_phase2(self):
  c=self.stage();self.submit(c);c=self.stage(c);self.cloud.lose='execute-change-set'
  with self.assertRaises(TimeoutError):self.submit(c)
  restarted=self.make_adapter(202);c=self.stage(c,restarted);self.assertEqual(c['operation']['state'],'phase2_create_intent');self.submit(c,restarted)
  self.assertEqual(self.cloud.mutations,['create-change-set','execute-change-set','create-change-set'])
 def test_repeat_dispatch_without_checkpoint_rejected(self):
  c=self.stage();self.submit(c)
  with self.assertRaises(ValueError):self.stage(adapter=self.make_adapter(202))
  self.assertEqual(len(self.cloud.mutations),1)
 def test_phase1_pending_failure_wrong_version_and_readiness_stop(self):
  c=self.stage();self.submit(c);c=self.stage(c);self.submit(c)
  for status in ['UPDATE_IN_PROGRESS','UPDATE_ROLLBACK_COMPLETE','UPDATE_FAILED']:
   self.cloud.status=status
   with self.subTest(status=status),self.assertRaises(ValueError):self.stage(c,self.make_adapter(202))
  self.cloud.status='UPDATE_COMPLETE'
  for bad in ['wrong_version','wrong_hash','not_ready','config_drift']:
   self.cloud.bad=bad
   with self.subTest(bad=bad),self.assertRaises(ValueError):self.stage(c,self.make_adapter(202))
  self.assertEqual(len(self.cloud.mutations),2)
 def test_wrong_main_blocks_before_mutation(self):
  c=self.stage();self.cloud.source='f'*40
  with self.assertRaises(ValueError):self.submit(c)
  self.assertFalse(self.cloud.mutations)
 def test_checkpoint_hash_and_review_pins_are_not_self_approved(self):
  bad=copy.deepcopy(self.review);bad['plan']['review']['source_commit']='f'*40;ident,hashed=self.cloud.upload_model(bad)
  with self.assertRaises(ValueError):self.adapter.read_artifact(ident,hashed,'java-review')
 def test_real_changeset_shape_uses_durable_own_response_evidence(self):
  c=self.stage();self.submit(c)
  self.assertNotIn('RoleARN',next(iter(self.cloud.sets.values())))
  next_checkpoint=self.stage(c)
  proof=next_checkpoint['operation']['phase1_change_set']['ExecutorEvidence']
  self.assertEqual(proof['role_arn'],d.EXECUTOR)
 def test_missing_or_wrong_operator_attribution_never_executes(self):
  c=self.stage();self.cloud.lose='create-change-set'
  with self.assertRaises(TimeoutError):self.submit(c)
  with self.assertRaises(ValueError):self.stage(c,self.make_adapter(202))
  bad=copy.deepcopy(self.cloud.events[0]);bad['requestParameters']['roleARN']='shared'
  with self.assertRaises(ValueError):self.stage(c,self.make_adapter(202),self.operator_receipt(c,bad))
  self.assertEqual(self.cloud.mutations,['create-change-set'])
 def test_create_response_artifact_upload_loss_requires_operator_receipt(self):
  c=self.stage();self.adapter.submit(c) # own AWS response exists locally, but never uploaded
  with self.assertRaises(ValueError):self.stage(c,self.make_adapter(202))
  next_checkpoint=self.stage(c,self.make_adapter(202),self.operator_receipt(c))
  self.assertEqual(next_checkpoint['operation']['state'],'phase1_execute_intent')
  self.assertEqual(self.cloud.mutations,['create-change-set'])
 def test_unapproved_operator_cannot_supply_exception_receipt(self):
  c=self.stage();self.cloud.lose='create-change-set'
  with self.assertRaises(TimeoutError):self.submit(c)
  receipt=self.operator_receipt(c);binding=receipt['_artifact_binding'];restricted=a.Adapter(self.cloud,[7],self.cloud.source,a.pinset(self.p),202,operator_actor_ids=[])
  with self.assertRaises(ValueError):restricted.read_artifact(binding['id'],binding['digest'],'java-operator-evidence')
 def test_wrong_workflow_or_environment_provenance_is_rejected(self):
  original=self.cloud.github
  for fault in ['workflow','environment','actor']:
   def bad(path):
    value=original(path)
    if '/actions/runs/' in path and fault=='workflow':value['path']='.github/workflows/other.yml'
    if '/actions/runs/' in path and fault=='actor':value['triggering_actor']['id']=999
    if '/deployments/' in path and not '/statuses?' in path and fault=='environment':value['environment']='staging'
    return value
   self.cloud.github=bad
   with self.subTest(fault=fault),self.assertRaises(ValueError):self.adapter.read_artifact(self.rid,self.rhash,'java-review')
   self.cloud.github=original
if __name__=='__main__':unittest.main()
