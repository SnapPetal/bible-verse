#!/usr/bin/env python3
"""Disabled first-migration route sharing authenticated artifacts and creation proofs with
ordinary Java releases. It permits an explicitly pinned old stack executor before rebind,
requires the dedicated executor afterward, and never queries CloudTrail.
"""
import argparse,copy,json,os,re,time
from pathlib import Path
import private_records as private
import bible_deploy as d
import creation_evidence as ce
from java_adapter import Adapter,Transport,REPO,digest,deployment_for_run,write_json,output
WORKFLOW='.github/workflows/deploy.yml'
OLD_EXECUTOR=f'arn:aws:iam::{d.ACCOUNT}:role/cdk-hnb659fds-cfn-exec-role-{d.ACCOUNT}-{d.REGION}'
class MigrationAdapter(Adapter):
 def __init__(self,transport,actors,source,pins,run_id,attempt=1,operators=()):
  super().__init__(transport,actors,source,pins,run_id,attempt,operators,WORKFLOW,'bible-deploy-')
 def plan_check(self,p):
  d.need(p.get('source_commit')==self.source and digest(p)==self.pins['plan_sha256'],'Migration plan/source differs from independent pins')
  d.need(p.get('kind')=='first-migration' and p.get('baseline_executor') in [OLD_EXECUTOR,d.EXECUTOR],'Unapproved pre-rebind executor')
  d.need(digest(p['baseline'])==p['baseline_sha256'] and digest(p['template'])==p['candidate_sha256'],'Reviewed template digest differs')
  d.need(p.get('capabilities')==['CAPABILITY_NAMED_IAM'],'Unreviewed capability acknowledgement')
  d.validate_candidate(p['template'],p['baseline'],{'candidate_sha256':p['candidate_sha256']})
  keep=lambda t:{k:v for k,v in t['Resources'].items() if v['Type'] in ['AWS::Lambda::Version','AWS::Lambda::Alias']}
  d.need(keep(p['template'])==keep(p['baseline']),'First migration may not publish versions or shift aliases')
 def stack(self,p,post=False):
  self.plan_check(p);self.main_and_actor()
  x=self.t.aws('cloudformation','describe-stacks',stack_name=d.STACK)['Stacks'][0]
  d.need(x['StackId']==d.STACK and x['StackStatus']=='UPDATE_COMPLETE','Migration stack is not exact/stable')
  d.need(x.get('RoleARN')==(d.EXECUTOR if post else p['baseline_executor']),'Stored stack executor differs from approved stage')
  template=d.original(self.t.aws('cloudformation','get-template',stack_name=d.STACK,template_stage='Original'))
  d.need(digest(template)==p['candidate_sha256' if post else 'baseline_sha256'],'Live migration template differs')
  return x
 def publication(self,p):
  for asset in p['assets']+[p['template_asset']]:
   d.need(asset['bucket']==d.BUCKET and asset['owner']==d.ACCOUNT and re.fullmatch(r'(?:assets/[a-f0-9]{64}\.(?:jar|zip)|reviews/[a-f0-9]{64}\.json)',asset['key']),'Wrong immutable asset destination')
   h=self.t.aws('s3api','head-object',bucket=d.BUCKET,key=asset['key'],version_id=asset['version_id'],expected_bucket_owner=d.ACCOUNT,checksum_mode='ENABLED');d.object_receipt(h,asset);d.need(h['VersionId']==asset['version_id'],'Wrong object version')
  a=p['template_asset'];raw=d.canonical(p['template'])
  d.need(a['key']=='reviews/'+p['candidate_sha256']+'.json' and a['bytes']==len(raw) and a['sha256_base64']==d.base64.b64encode(d.hashlib.sha256(raw).digest()).decode(),'Published template is not reviewed template')
  receipts={x['key']:x for x in p['assets']};used=set()
  for resource in p['template']['Resources'].values():
   props=resource.get('Properties',{});code=props.get('Code') if resource['Type']=='AWS::Lambda::Function' else props.get('Content') if resource['Type']=='AWS::Lambda::LayerVersion' else None
   if isinstance(code,dict) and code.get('S3Bucket')==d.BUCKET:
    key=code.get('S3Key');d.need(key in receipts and code.get('S3ObjectVersion')==receipts[key]['version_id'],'Unpinned dedicated code/layer');used.add(key)
   if resource['Type']=='Custom::CDKBucketDeployment':
    for bucket,key in zip(props.get('SourceBucketNames',[]),props.get('SourceObjectKeys',[])):
     if bucket==d.BUCKET:
      d.need(key in receipts,'Missing immutable deployment source');used.add(key)
      h=self.t.aws('s3api','head-object',bucket=d.BUCKET,key=key,expected_bucket_owner=d.ACCOUNT,checksum_mode='ENABLED');d.object_receipt(h,receipts[key]);d.need(h['VersionId']==receipts[key]['version_id'],'Current data source version changed')
  d.need(used==set(receipts),'Missing or extra asset receipt')
  policy=json.loads(self.t.aws('s3api','get-bucket-policy',bucket=d.BUCKET,expected_bucket_owner=d.ACCOUNT)['Policy']);d.need(digest(policy)==p['asset_policy_sha256'],'Asset policy changed')
  d.need(self.t.aws('s3api','get-bucket-versioning',bucket=d.BUCKET,expected_bucket_owner=d.ACCOUNT).get('Status')=='Enabled','Versioning changed')
  import subprocess
  try:life=self.t.aws('s3api','get-bucket-lifecycle-configuration',bucket=d.BUCKET,expected_bucket_owner=d.ACCOUNT);d.need(not life.get('Rules'),'Unexpected asset expiry')
  except subprocess.CalledProcessError as e:d.need('(NoSuchLifecycleConfiguration)' in (e.stderr or ''),'Lifecycle absence unverified')
 def read_record(self,ident,hashed,kind):
  filename={'migration-intent':'operation.json','migration-response':'response.json','migration-operator-evidence':'operator-evidence.json'}[kind]
  value,meta=self.read_envelope(ident,hashed,kind,filename,operator=kind=='migration-operator-evidence',review=kind=='migration-operator-evidence')
  prefix={'migration-intent':'bible-migration-intent-','migration-response':'bible-migration-response-','migration-operator-evidence':'bible-migration-operator-'}[kind]
  d.need(meta['name']==prefix+self.pins['plan_sha256']+'-'+value['state'],'Wrong migration artifact name')
  if kind=='migration-intent':self.plan_check(value['plan']);d.need(value['request_sha256']==digest(value['request']),'Intent request hash differs')
  return value
 def new_record(self,p,state,request):
  return {'kind':'migration-intent','state':state,'plan':p,'plan_sha256':digest(p),'source_commit':self.source,'run_id':self.run_id,'run_attempt':self.attempt,'created_at':self.t.now().isoformat(),'request':request,'request_sha256':digest(request)}
 def prepare_intent(self,p):
  self.stack(p);self.publication(p);d.need(self.attempt==1,'Rerun requires reconciliation')
  name='bible-isolated-update-migration-'+digest(p)[:32]
  d.need(self.describe(name) is None,'Existing deterministic change set; reconcile instead of create')
  # Same full repository artifact listing/pagination as the ordinary adapter, with migration prefix.
  found=[]
  for page in range(1,101):
   rows=self.t.github(f'repos/{REPO}/actions/artifacts?per_page=100&page={page}')['artifacts'];found.extend(x for x in rows if x['name'].startswith('bible-migration-intent-'+digest(p)+'-'))
   if len(rows)<100:break
  else:raise ValueError('Artifact discovery incomplete')
  d.need(not found,'Existing durable migration intent requires reconciliation')
  from urllib.parse import quote
  a=p['template_asset'];request={'StackName':d.STACK,'ChangeSetName':name,'ChangeSetType':'UPDATE','RoleARN':d.EXECUTOR,'Capabilities':p['capabilities'],'TemplateURL':f'https://{d.BUCKET}.s3.{d.REGION}.amazonaws.com/{a["key"]}?versionId={quote(a["version_id"],safe="")}','ClientToken':digest([digest(p),'create']),'Description':'first-migration:'+digest(p)}
  return self.new_record(p,'create',request)
 def bind_creation(self,intent,response=None,operator=None):
  self.plan_check(intent['plan']);d.need(intent['state']=='create','Not a create intent');request=intent['request'];cs=self.describe(request['ChangeSetName']);d.need(cs is not None,'Uncertain creation absent; never retry automatically')
  receipt=response if response is not None else operator;d.need(receipt is not None,'No durable own success response; bounded operator reconciliation required')
  binding=intent['_artifact_binding'];d.need(receipt['intent_artifact_id']==binding['id'] and receipt['intent_artifact_digest']==binding['digest'] and receipt['request_sha256']==digest(request),'Receipt does not bind exact immutable intent')
  if response is not None:
   d.need(response['kind']=='migration-response','Wrong own-response type');proof=ce.own_response(request,cs,response,intent['run_id'])
  else:
   d.need(operator['kind']=='migration-operator-evidence','Wrong operator receipt type');proof=ce.operator_event(request,{'run_id':intent['run_id'],'created_at':intent['created_at']},operator['event'],cs,self.t.now(),self.session_prefix)
  cs['ExecutorEvidence']=proof
  d.need(cs.get('ChangeSetName')==request['ChangeSetName'] and cs.get('Description')==request['Description'] and cs.get('Capabilities')==request['Capabilities'],'Created change set differs from request')
  return cs
 def execute_intent(self,intent,response,operator,approved_arn,approved_changes):
  p=intent['plan'];self.stack(p);self.publication(p);cs=self.bind_creation(intent,response,operator)
  template=self.cs_template(cs['ChangeSetId']);actual=d.check_changes(cs,{'candidate_sha256':p['candidate_sha256'],'baseline':p['baseline']},template,approved_arn)
  d.need(actual==approved_changes,'Actual changes differ from authenticated review')
  request={'StackName':d.STACK,'ChangeSetName':cs['ChangeSetId'],'ClientRequestToken':digest([digest(p),'execute'])}
  record=self.new_record(p,'execute',request);record.update(create_intent=intent,creation_proof=cs['ExecutorEvidence'],reviewed_change_set=cs,changes_sha256=actual);return record
 def submit(self,record):
  d.need(record['run_id']==self.run_id and record['run_attempt']==self.attempt==1,'Resume must reconcile, not reissue')
  p=record['plan'];self.stack(p);self.publication(p);request=record['request']
  if record['state']=='create':
   d.need(request['RoleARN']==d.EXECUTOR and request['StackName']==d.STACK and request['ChangeSetName']=='bible-isolated-update-migration-'+digest(p)[:32],'Wrong create request')
   d.need(self.describe(request['ChangeSetName']) is None,'Existing server request; reconcile')
   return self.t.aws('cloudformation','create-change-set',stack_name=d.STACK,change_set_name=request['ChangeSetName'],change_set_type='UPDATE',role_arn=d.EXECUTOR,template_url=request['TemplateURL'],client_token=request['ClientToken'],description=request['Description'],capabilities=request['Capabilities'])
  d.need(record['state']=='execute','Unknown mutation')
  cs=self.describe(request['ChangeSetName']);d.need(cs is not None,'Reviewed change set disappeared');cs['ExecutorEvidence']=record['creation_proof']
  d.need(cs==record['reviewed_change_set'],'Actual change set differs');template=self.cs_template(cs['ChangeSetId'])
  d.need(d.check_changes(cs,{'candidate_sha256':p['candidate_sha256'],'baseline':p['baseline']},template,request['ChangeSetName'])==record['changes_sha256'],'Changed change-set digest')
  self.validation_gate(cs)
  return self.t.aws('cloudformation','execute-change-set',stack_name=d.STACK,change_set_name=request['ChangeSetName'],client_request_token=request['ClientRequestToken'])
 def postflight(self,execute_intent):
  d.need(execute_intent['state']=='execute','Wrong execution intent');p=execute_intent['plan'];self.stack(p,post=True)
  cs=self.describe(execute_intent['request']['ChangeSetName']);d.need(cs is not None and cs.get('StackId')==d.STACK and cs.get('ExecutionStatus')=='EXECUTE_COMPLETE','Execution not confirmed')
  cs['ExecutorEvidence']=execute_intent['creation_proof'];ce.executor_verified(cs)
  return {'stack_id':d.STACK,'executor':d.EXECUTOR,'template_sha256':p['candidate_sha256'],'change_set_arn':cs['ChangeSetId'],'complete':True}

def dispatch():
 c=d.active();d.need(c.get('migration_adapter_enabled') is True,'Migration adapter disabled')
 d.need(c.get('private_checkpoint_upload_approved') is True and c.get('public_checkpoint_reference_sharing_approved') is True,'Private persistence/public-reference disclosure not approved')
 d.need(os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_REF')=='refs/heads/main' and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/'+WORKFLOW+'@refs/heads/main' and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch','Wrong workflow context')
 event=d.load(os.environ['GITHUB_EVENT_PATH']);inputs=event['inputs'];operator=inputs['mode']=='operator-reconcile';actors=c.get('migration_operator_actor_ids' if operator else 'migration_actor_ids',[])
 d.need(event['sender']['id'] in actors and int(os.environ.get('GITHUB_ACTOR_ID','0')) in actors,'Unapproved dispatch actor');d.source_gate()
 pins=json.loads(inputs['migration_pins']);source=os.environ['GITHUB_SHA'];d.need(pins['source_commit']==source,'Wrong source pins')
 return c,inputs,MigrationAdapter(Transport(),c.get('migration_actor_ids',[]),source,pins,os.environ['GITHUB_RUN_ID'],os.environ.get('GITHUB_RUN_ATTEMPT','1'),c.get('migration_operator_actor_ids',[]))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['gate','stage','submit','review','postflight','operator']);ap.add_argument('--out',type=Path,default=Path('migration-receipts'));args=ap.parse_args();c,inputs,a=dispatch()
 if args.action=='gate':return
 args.out.mkdir(parents=True,exist_ok=True);artifacts=json.loads(inputs.get('artifacts') or '{}');d.need(set(artifacts)<= {'intent_id','intent_digest','response_id','response_digest','operator_id','operator_digest','operator_private_ref'},'Only bounded artifact references are accepted; no raw operator events')
 if args.action=='stage':
  if inputs['mode']=='prepare':
   p=a.t.private_record(private.validate_reference(json.loads(inputs['bundle_ref'])));d.need(p.get('source_commit') in [None,a.source],'Stored source pin conflicts');p={**p,'source_commit':a.source}
   d.need(p['baseline_sha256']==c['baseline_sha256'] and p['candidate_sha256']==c['candidate_sha256'] and p['baseline_executor']==c['baseline_executor'],'Plan differs from activated bounded migration');record=a.prepare_intent(p)
  else:
   d.need(inputs['mode']=='execute','Wrong mode');intent=a.read_record(artifacts['intent_id'],artifacts['intent_digest'],'migration-intent')
   response=a.read_record(artifacts['response_id'],artifacts['response_digest'],'migration-response') if artifacts.get('response_id') else None
   operator=a.read_record(artifacts['operator_id'],artifacts['operator_digest'],'migration-operator-evidence') if artifacts.get('operator_id') else None
   d.need(0<=a.t.now().timestamp()-float(inputs['reviewed_at'])<=900,'Change-set review expired')
   record=a.execute_intent(intent,response,operator,inputs['change_set_arn'],inputs['changes_sha256'])
  deployment=deployment_for_run(a);a.provenance(a.run_id,deployment);record['deployment_id']=deployment;write_json(args.out/'operation.json',record);output({'artifact_name':'bible-migration-intent-'+a.pins['plan_sha256']+'-'+record['state']});return
 if args.action=='operator':
  intent=a.read_record(artifacts['intent_id'],artifacts['intent_digest'],'migration-intent');d.need(intent['state']=='create','Only uncertain create reconciliation')
  bundle=a.t.private_record(private.validate_reference(artifacts['operator_private_ref']));event=bundle['event'];d.need(digest(event)==bundle['event_sha256'],'Operator event differs from independent digest');event=private.bounded_operator_event(event);cs=a.describe(intent['request']['ChangeSetName']);d.need(cs is not None,'Uncertain request absent; no retry')
  ce.operator_event(intent['request'],{'run_id':intent['run_id'],'created_at':intent['created_at']},event,cs,a.t.now(),a.session_prefix)
  deployment=deployment_for_run(a);a.provenance(a.run_id,deployment,operator=True)
  record={'kind':'migration-operator-evidence','state':'create','plan_sha256':a.pins['plan_sha256'],'source_commit':a.source,'run_id':a.run_id,'deployment_id':deployment,'intent_artifact_id':int(artifacts['intent_id']),'intent_artifact_digest':artifacts['intent_digest'],'request_sha256':intent['request_sha256'],'event':event};write_json(args.out/'operator-evidence.json',record);output({'artifact_name':'bible-migration-operator-'+a.pins['plan_sha256']+'-create'});return
 intent=a.read_record(os.environ['INTENT_ARTIFACT_ID'],os.environ['INTENT_ARTIFACT_DIGEST'],'migration-intent')
 if args.action=='review':
  d.need(intent['state']=='create','Review requires create intent')
  response=a.read_record(os.environ['RESPONSE_ARTIFACT_ID'],os.environ['RESPONSE_ARTIFACT_DIGEST'],'migration-response')
  deadline=time.monotonic()+180
  while True:
   cs=a.describe(intent['request']['ChangeSetName']);d.need(cs is not None,'Uncertain create absent; do not retry')
   if cs.get('Status') not in ['CREATE_PENDING','CREATE_IN_PROGRESS']:break
   d.need(time.monotonic()<deadline,'Creation pending; reconcile read-only');time.sleep(3)
  cs=a.bind_creation(intent,response);template=a.cs_template(cs['ChangeSetId']);changes=d.check_changes(cs,{'candidate_sha256':intent['plan']['candidate_sha256'],'baseline':intent['plan']['baseline']},template,cs['ChangeSetId'])
  write_json(args.out/'change-set-review.json',{'kind':'migration-review','change_set_arn':cs['ChangeSetId'],'changes_sha256':changes,'changes':cs['Changes'],'template_sha256':digest(template),'creation_proof':cs['ExecutorEvidence'],'reviewed_at':a.t.now().timestamp(),'not_executed':True});return
 if args.action=='postflight':
  deadline=time.monotonic()+600
  while True:
   cs=a.describe(intent['request']['ChangeSetName'])
   d.need(cs is not None,'Uncertain execute disappeared')
   if cs.get('ExecutionStatus')!='EXECUTE_IN_PROGRESS':break
   d.need(time.monotonic()<deadline,'Migration still pending; reconcile without resubmission');time.sleep(5)
  write_json(args.out/'postflight.json',a.postflight(intent));return
 d.exclusive_intent(args.out/'submit-gate.json',{'artifact_id':os.environ['INTENT_ARTIFACT_ID'],'artifact_digest':os.environ['INTENT_ARTIFACT_DIGEST']});result=a.submit(intent)
 response={'kind':'migration-response','state':intent['state'],'plan_sha256':a.pins['plan_sha256'],'source_commit':a.source,'run_id':a.run_id,'deployment_id':intent['deployment_id'],'intent_artifact_id':int(os.environ['INTENT_ARTIFACT_ID']),'intent_artifact_digest':os.environ['INTENT_ARTIFACT_DIGEST'],'request_sha256':intent['request_sha256'],'response':result};write_json(args.out/'response.json',response);output({'artifact_name':'bible-migration-response-'+a.pins['plan_sha256']+'-'+intent['state']})
if __name__=='__main__':main()
