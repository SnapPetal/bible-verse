#!/usr/bin/env python3
"""Disabled CLI/AWS/GitHub adapter for the two-phase protocol. All cloud access is injected
through Transport in tests; production CLI gates activation before any cloud call.
"""
import argparse, base64, copy, hashlib, io, json, os, re, subprocess, tempfile, zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
import private_records as private
import creation_evidence as ce
import bible_deploy as d
import java_release as j
REPO='SnapPetal/bible-verse'; WORKFLOW='.github/workflows/java-release.yml'; ENVIRONMENT='production'
def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def digest(x):return d.sha(d.canonical(x))
def pinset(plan):
 return {'plan_sha256':digest(plan),'source_commit':plan['review']['source_commit'],**{k:plan['review'][k] for k in ['baseline_sha256','candidate_sha256','assets_sha256','phase1_sha256','phase2_sha256']}}
class Transport:
 def __init__(self):self.server_time=None
 def aws(self,service,operation,**kw):return d.aws(service,operation,**kw)
 def github(self,path):
  result=subprocess.run(['gh','api','--include',path],check=True,capture_output=True).stdout
  # JSON calls only. --include has one HTTP header block for ordinary GitHub API GETs.
  normalized=result.replace(b'\r\n',b'\n');headers,body=normalized.split(b'\n\n',1)
  dates=[line.split(b':',1)[1].strip().decode() for line in headers.splitlines() if line.lower().startswith(b'date:')]
  d.need(len(dates)==1,'Missing trusted GitHub server date');self.server_time=parsedate_to_datetime(dates[0]).astimezone(timezone.utc)
  return json.loads(body)
 def archive(self,artifact_id):
  return subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{artifact_id}/zip'],check=True,capture_output=True).stdout
 def private_record(self,ref):return private.fetch(ref)
 def now(self):
  d.need(self.server_time is not None,'No trusted server clock');return self.server_time

class Adapter:
 def __init__(self,transport,actor_ids,source,pins,run_id,attempt=1,operator_actor_ids=(),workflow=WORKFLOW,session_prefix="bible-java-"):
  self.t=transport;self.actor_ids={int(x) for x in actor_ids};self.source=source;self.pins=pins;self.run_id=int(run_id);self.attempt=int(attempt);self.operator_actor_ids={int(x) for x in operator_actor_ids};self.workflow=workflow;self.session_prefix=session_prefix
  d.need(self.actor_ids and pins.get('source_commit')==source,'Missing approved actor/source pins')
 def main_and_actor(self):
  head=self.t.github(f'repos/{REPO}/git/ref/heads/main');d.need(head['object']['sha']==self.source,'Current main changed')
  x=self.t.aws('sts','get-caller-identity')
  d.need(x.get('Account')==d.ACCOUNT and x.get('Arn')==f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkDeploy/{self.session_prefix}{self.run_id}','Wrong deploy actor')
 def provenance(self,run_id,deployment_id,review=False,operator=False):
  run=self.t.github(f'repos/{REPO}/actions/runs/{run_id}')
  d.need(run.get('repository',{}).get('full_name')==REPO and run.get('head_repository',{}).get('full_name')==REPO,'Wrong repository provenance')
  d.need(run.get('path')==self.workflow and run.get('head_sha')==self.source and run.get('head_branch')=='main' and run.get('event')=='workflow_dispatch','Wrong workflow/source provenance')
  allowed=self.operator_actor_ids if operator else self.actor_ids
  d.need(run.get('triggering_actor',{}).get('id') in allowed and run.get('actor',{}).get('id') in allowed,'Unapproved dispatch actor')
  if review:d.need(run.get('status')=='completed' and run.get('conclusion')=='success','Review run not successfully complete')
  deployment=self.t.github(f'repos/{REPO}/deployments/{deployment_id}')
  d.need(deployment.get('environment')==ENVIRONMENT and deployment.get('sha')==self.source and deployment.get('performed_via_github_app',{}).get('slug')=='github-actions','Wrong production deployment evidence')
  statuses=self.t.github(f'repos/{REPO}/deployments/{deployment_id}/statuses?per_page=100')
  # Establish a real Actions environment record tied to this run, not an asserted string in a ZIP.
  expected=f'https://github.com/{REPO}/actions/runs/{run_id}'
  d.need(any(x.get('environment')==ENVIRONMENT and (x.get('log_url')==expected or x.get('log_url','').startswith(expected+'/job/')) and x.get('state') in ['in_progress','success','failure','error','inactive'] for x in statuses),'Production environment/run link unverified')
  return run
 def read_envelope(self,artifact_id,expected_zip_digest,kind,filename,review=False,operator=False):
  d.need(str(artifact_id).isdigit() and re.fullmatch('sha256:[a-f0-9]{64}',expected_zip_digest or ''),'Missing exact artifact identity/digest')
  meta=self.t.github(f'repos/{REPO}/actions/artifacts/{artifact_id}')
  d.need(meta.get('id')==int(artifact_id) and not meta.get('expired') and meta.get('digest')==expected_zip_digest,'Artifact expired, wrong or tampered')
  d.need(utc(meta['expires_at'])>self.t.now() and utc(meta['created_at'])<=self.t.now() and (utc(meta['expires_at'])-utc(meta['created_at'])).total_seconds()<=3*86400+300,'Artifact outside three-day retention')
  raw=self.t.archive(artifact_id);d.need('sha256:'+hashlib.sha256(raw).hexdigest()==expected_zip_digest,'Downloaded artifact digest differs')
  d.need(len(raw)<=65536,'Public reference archive too large')
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   names=z.infolist()
   d.need(not z.comment and not any(x.comment or x.extra for x in names),'Public artifact free-text/extra ZIP metadata is forbidden')
   d.need(len(names)==1 and names[0].filename=='record-ref.json' and names[0].file_size<=16384 and not names[0].flag_bits&1,'Public artifact must contain only record-ref.json')
   public_bytes=z.read('record-ref.json');ref=private.validate_reference(json.loads(public_bytes));d.need(public_bytes==d.canonical(ref),'Public reference must be canonical, without extra metadata')
  value=self.t.private_record(ref)
  d.need(value.get('kind')==kind and value.get('source_commit')==self.source,'Wrong artifact content/source')
  run_id=meta['workflow_run']['id'];d.need(value.get('run_id')==run_id and meta['workflow_run'].get('head_sha')==self.source,'Artifact/run identity differs')
  self.provenance(run_id,value['deployment_id'],review=review,operator=operator)
  d.need(value.get('plan_sha256')==self.pins['plan_sha256'],'Artifact plan differs from dispatch')
  value['_artifact_binding']={'id':int(artifact_id),'digest':expected_zip_digest}
  return value,meta
 def read_artifact(self,artifact_id,expected_zip_digest,kind):
  filename={'java-review':'review.json','java-intent':'operation.json','java-response':'response.json','java-operator-evidence':'operator-evidence.json'}[kind]
  value,meta=self.read_envelope(artifact_id,expected_zip_digest,kind,filename,review=kind in ['java-review','java-operator-evidence'],operator=kind=='java-operator-evidence')
  if kind=='java-review':
   p=value['plan'];checked=j.derive(p['baseline'],value['candidate'],p['assets'],p['review'])
   d.need(checked==p and pinset(p)==self.pins,'Review pin or derived plan differs')
   d.need(utc(value['expires_at'])>self.t.now(),'Release review expired')
   d.need(meta['name']=='bible-java-review-'+digest(p),'Wrong review artifact name')
  elif kind=='java-intent':
   r=value['operation'];d.need(digest(r['plan'])==r['plan_digest']==self.pins['plan_sha256'],'Checkpoint plan altered')
   d.need(pinset(r['plan'])==self.pins and value['request_sha256']==digest(r['history'][-1]['request']),'Checkpoint request altered')
   d.need(re.fullmatch('phase[12]_(create|execute)_intent',r['state']),'Not a pre-action checkpoint')
   d.need(meta['name']=='bible-java-intent-'+r['plan_digest']+'-'+r['state'],'Wrong intent artifact name')
  else:
   prefix='bible-java-response-' if kind=='java-response' else 'bible-java-operator-'
   d.need(meta['name']==prefix+value['plan_sha256']+'-'+value['intent_state'],'Wrong receipt artifact name')
  value['_artifact_binding']={'id':int(artifact_id),'digest':expected_zip_digest}
  return value
 def observation(self,plan,phase,alias_mode):
  self.main_and_actor()
  stack=self.t.aws('cloudformation','describe-stacks',stack_name=d.STACK)['Stacks'][0]
  template=d.original(self.t.aws('cloudformation','get-template',stack_name=d.STACK,template_stage='Original'))
  e={'account':d.ACCOUNT,'region':d.REGION,'caller_role':j.DEPLOY,'stack_id':stack['StackId'],'executor':stack.get('RoleARN'),'current_main':self.source,'checked_out':self.source,'stack_status':stack['StackStatus'],'template':template,'functions':{}}
  for fid,info in j.FUNCTIONS.items():
   old=plan['review']['old_versions'][fid];mapping=old['physical_id']
   if phase!='phase2':
    mapped=self.t.aws('cloudformation','describe-stack-resource',stack_name=d.STACK,logical_resource_id=old['logical_id'])['StackResourceDetail'];d.need(mapped['LogicalResourceId']==old['logical_id'] and mapped['ResourceType']=='AWS::Lambda::Version','Wrong old CFN mapping');mapping=mapped['PhysicalResourceId']
   old_config=self.t.aws('lambda','get-function-configuration',function_name=info['name'],qualifier=old['version'])
   latest=self.t.aws('lambda','get-function-configuration',function_name=info['name'])
   d.need(latest.get('FunctionArn')==f'arn:aws:lambda:{d.REGION}:{d.ACCOUNT}:function:{info["name"]}' and latest.get('Version')=='$LATEST','Wrong unqualified function observation')
   d.need(latest.get('State')=='Active' and latest.get('LastUpdateStatus')=='Successful','Unqualified function update not ready')
   expected_latest=old['code_sha256'] if phase=='baseline' else plan['assets'][fid]['sha256_base64']
   d.need(latest.get('CodeSha256')==expected_latest,'Unqualified code differs from expected stage')
   for key in ['Runtime','Handler','Architectures','MemorySize','Timeout','Environment','Layers','KMSKeyArn','VpcConfig','DeadLetterConfig','TracingConfig','EphemeralStorage','FileSystemConfigs','LoggingConfig','Role']:
    d.need(latest.get(key)==old_config.get(key),'Unqualified runtime configuration drift: '+key)
   d.need(all(key in latest for key in ['Runtime','Handler','MemorySize','Timeout','Role']),'Incomplete unqualified configuration')
   d.need('Error' not in latest.get('Environment',{}),'Unqualified Environment verification unavailable')
   d.need(latest.get('SnapStart',{}).get('ApplyOn')==old_config.get('SnapStart',{}).get('ApplyOn')=='PublishedVersions','Unqualified SnapStart configuration drift')
   alias=self.t.aws('lambda','get-alias',function_name=info['name'],name='live')
   x={'old':old_config,'alias':alias,'old_logical_id':old['logical_id'],'old_physical_id':mapping,'old_mapping_source':'recorded-baseline' if phase=='phase2' else 'current-stack'}
   if phase!='baseline':
    logical=plan['versions'][fid]['new_logical'];mapped=self.t.aws('cloudformation','describe-stack-resource',stack_name=d.STACK,logical_resource_id=logical)['StackResourceDetail'];d.need(mapped['LogicalResourceId']==logical and mapped['ResourceType']=='AWS::Lambda::Version','Wrong new CFN mapping')
    physical=mapped['PhysicalResourceId'];number=physical.rsplit(':',1)[-1];d.need(re.fullmatch('[1-9][0-9]*',number),'Unqualified new version')
    x.update(new=self.t.aws('lambda','get-function-configuration',function_name=info['name'],qualifier=number),new_logical_id=logical,new_physical_id=physical)
    # Ordinary code releases may not silently publish a changed runtime configuration.
    for key in ['Runtime','Handler','Architectures','MemorySize','Timeout','Environment','Layers','KMSKeyArn','VpcConfig','DeadLetterConfig','TracingConfig','EphemeralStorage','FileSystemConfigs','LoggingConfig','Role']:
     d.need(x['new'].get(key)==old_config.get(key),'Runtime configuration drift: '+key)
    d.need(all(key in old_config for key in ['Runtime','Handler','MemorySize','Timeout','Role']),'Incomplete function configuration')
   keep={'FunctionArn','Version','CodeSha256','Role','State','SnapStart','Runtime','Handler','Architectures','MemorySize','Timeout','Layers','KMSKeyArn','VpcConfig','DeadLetterConfig','TracingConfig','EphemeralStorage','FileSystemConfigs','LoggingConfig','EnvironmentSha256'}
   for kind in ['old','new']:
    if kind in x:
     if 'Environment' in x[kind]:x[kind]['EnvironmentSha256']=digest(x[kind].pop('Environment'))
     x[kind]={k:v for k,v in x[kind].items() if k in keep}
   x['alias']={k:v for k,v in x['alias'].items() if k in {'AliasArn','Name','FunctionVersion','RoutingConfig'}}
   e['functions'][fid]=x
  j.snapshot(e,plan,phase,alias_mode)
  return e
 def immutable_objects(self,plan,phase,template_receipt):
  for fid,a in plan['assets'].items():
   h=self.t.aws('s3api','head-object',bucket=d.BUCKET,key=a['key'],version_id=a['version_id'],expected_bucket_owner=d.ACCOUNT,checksum_mode='ENABLED')
   d.object_receipt(h,a);d.need(h['VersionId']==a['version_id'],'Wrong object version')
  a=template_receipt;template=plan[f'phase{phase}'];data=d.canonical(template)
  d.need(a['key']=='reviews/'+d.sha(data)+'.json' and a['sha256']==d.sha(data) and a['bytes']==len(data) and a['sha256_base64']==base64.b64encode(hashlib.sha256(data).digest()).decode(),'Stage template receipt differs')
  h=self.t.aws('s3api','head-object',bucket=d.BUCKET,key=a['key'],version_id=a['version_id'],expected_bucket_owner=d.ACCOUNT,checksum_mode='ENABLED');d.object_receipt(h,a);d.need(h['VersionId']==a['version_id'],'Wrong template version')
 def describe(self,name):
  try:
   result=self.t.aws('cloudformation','describe-change-set',stack_name=d.STACK,change_set_name=name,include_property_values=True)
   d.need(not result.get('NextToken'),'Incomplete enhanced change set; stop')
   if result.get('Status')=='FAILED':self.validation_gate(result)
   return result
  except subprocess.CalledProcessError as error:
   message=error.stderr or ''
   if '(ChangeSetNotFound)' in message or ('(ValidationError)' in message and 'ChangeSet' in message and 'does not exist' in message):return None
   raise
 def validation_gate(self,cs):
  # Exact terminal change set only. Raw events may contain Environment/properties:
  # consume in memory, never persist or interpolate server strings into errors.
  d.need(cs.get('StackId')==d.STACK and cs.get('Status') in ['CREATE_COMPLETE','FAILED'],'Validation requires terminal exact-stack change set')
  name=cs.get('ChangeSetId');d.need(isinstance(name,str) and name.startswith(f'arn:aws:cloudformation:{d.REGION}:{d.ACCOUNT}:changeSet/'),'Wrong validation change set')
  token=None;seen=set();count=0;blocked=False
  for page in range(100):
   kw={'stack_name':d.STACK,'change_set_name':name}
   if token:kw['next_token']=token
   result=self.t.aws('cloudformation','describe-events',**kw)
   events=result.get('OperationEvents');d.need(isinstance(events,list),'Missing validation event collection')
   for event in events:
    d.need(isinstance(event,dict),'Malformed validation event')
    d.need(event.get('StackId',d.STACK)==d.STACK,'Foreign validation event')
    kind=event.get('EventType');d.need(kind in ['STACK_EVENT','PROGRESS_EVENT','VALIDATION_ERROR','PROVISIONING_ERROR','HOOK_INVOCATION_ERROR'],'Unknown validation event type')
    count+=1
    # Warnings/skipped checks require bounded review too; no automatic acceptance.
    if kind in ['VALIDATION_ERROR','PROVISIONING_ERROR','HOOK_INVOCATION_ERROR'] or event.get('DetailedStatus')=='VALIDATION_FAILED' or event.get('OperationStatus')=='FAILED':blocked=True
   token=result.get('NextToken')
   if not token:
    d.need(not blocked and cs['Status']=='CREATE_COMPLETE','Predeployment findings require private review; execution blocked')
    return {'checked':True,'event_count':count,'pages':page+1}
   d.need(isinstance(token,str) and len(token)<=1024 and token not in seen,'Incomplete or repeated validation pagination');seen.add(token)
  raise ValueError('Validation pagination exceeds bounded read limit')
 def cs_template(self,name):return d.original(self.t.aws('cloudformation','get-template',stack_name=d.STACK,change_set_name=name,template_stage='Original'))
 def operator_event(self,r,phase,event,cs):
  request=next(x for x in r['history'] if x.get('state')==f'phase{phase}_create_intent')['request'];origin=r['intent_origins'][f'phase{phase}_create_intent']
  return ce.operator_event(request,origin,event,cs,self.t.now(),self.session_prefix)
 def verified_change_set(self,r,phase):
  intent=next(x for x in r['history'] if x.get('state')==f'phase{phase}_create_intent')['request'];cs=self.describe(intent['ChangeSetName'])
  d.need(cs is not None,'Uncertain create absent; do not reissue')
  proof=r.get('creation_receipts',{}).get(str(phase))
  d.need(proof is not None,'Create lacks trustworthy own response; operator read-only reconciliation required')
  d.need(proof['request_sha256']==digest(intent) and proof['change_set_arn']==cs['ChangeSetId'] and proof['role_arn']==d.EXECUTOR,'Creation receipt differs')
  cs['ExecutorEvidence']=proof;return cs
 def creation_proof(self,checkpoint,phase,response=None,operator=None):
  r=checkpoint['operation'];intent=r['history'][-1]['request'];cs=self.describe(intent['ChangeSetName'])
  d.need(cs is not None,'Uncertain create absent; stop, never reissue')
  receipt=response if response is not None else operator
  d.need(receipt is not None,'No durable successful response; operator read-only reconciliation required')
  binding=checkpoint['_artifact_binding'];d.need(receipt['intent_artifact_id']==binding['id'] and receipt['intent_artifact_digest']==binding['digest'] and receipt['request_sha256']==digest(intent) and receipt['intent_state']==r['state'],'Receipt does not bind exact pre-action intent')
  if response is not None:
   d.need(response['run_id']==checkpoint['run_id'] and response['kind']=='java-response','Wrong response producer')
   return ce.own_response(intent,cs,response,checkpoint['run_id'])
  d.need(operator['kind']=='java-operator-evidence','Wrong operator receipt')
  return self.operator_event(r,phase,operator['event'],cs)
 def outstanding_artifacts(self,plan_hash):
  # Full pagination, including expired metadata. Missing local state is never a new-run authorization.
  found=[]
  for page in range(1,101):
   x=self.t.github(f'repos/{REPO}/actions/artifacts?per_page=100&page={page}');items=x['artifacts'];found.extend(a for a in items if a['name'].startswith('bible-java-intent-'+plan_hash+'-'))
   if len(items)<100:return found
  raise ValueError('Artifact discovery incomplete')
 def next_intent(self,review,op_path,template_receipts,resume=None,response=None,operator=None):
  p=review['plan'];op=j.Operation(op_path)
  if resume:
   d.need(not op.path.exists(),'Refuse local overwrite');op.path.parent.mkdir(parents=True,exist_ok=True);op.write(resume['operation'])
   # Every restored intent is uncertain. Read-only reconciliation happens first, never reissue.
   state=resume['operation']['state'];phase=int(state[5]);kind=state.split('_')[1];request=resume['operation']['history'][-1]['request']
   if kind=='create':
    r=json.loads(op.path.read_text());r.setdefault('creation_receipts',{})[str(phase)]=self.creation_proof(resume,phase,response,operator);op.write(r)
   cs=self.verified_change_set(json.loads(op.path.read_text()),phase)
   d.need(cs is not None,'Uncertain request not found: stop for bounded review; do not reissue')
   if kind=='create':op.reconcile_create(phase,cs,self.cs_template(cs['ChangeSetId']))
   else:
    e=self.observation(p,f'phase{phase}','old' if phase==1 else 'new');e['change_set']=cs;op.reconcile_execute(phase,e)
  else:
   d.need(self.attempt==1 and not self.outstanding_artifacts(digest(p)),'Existing/retried operation requires an authenticated checkpoint')
   # Probe both deterministic create names even when no local or GitHub artifact was supplied.
   for phase in [1,2]:
    token=d.sha(d.canonical([digest(p),phase,'create']));name='bible-isolated-update-java-'+token[:32]
    d.need(self.describe(name) is None,'Existing server operation requires checkpoint reconciliation')
   op.start(p,self.observation(p,'baseline','old'))
  r=json.loads(op.path.read_text());state=r['state']
  if state=='complete':return None
  phase=2 if state.startswith('phase2') or state=='phase1_ready' else 1
  kind='execute' if state.endswith('_prepared') else 'create'
  e=self.observation(p,'baseline' if phase==1 else 'phase1','old');self.immutable_objects(p,phase,template_receipts[f'phase{phase}'])
  if kind=='execute':
   cs=r[f'phase{phase}_change_set'];e['change_set']=self.verified_change_set(r,phase);e['change_set_template']=self.cs_template(cs['ChangeSetId'])
  op.request(phase,kind,e,template_receipts[f'phase{phase}'])
  return json.loads(op.path.read_text())
 def submit(self,checkpoint,artifact_verified_same_run=True):
  # A NEW same-run intent artifact authorizes exactly one submission attempt. Any restored prior-run
  # artifact must enter next_intent's read-only reconciliation, never this path.
  d.need(artifact_verified_same_run and checkpoint['run_id']==self.run_id and checkpoint['run_attempt']==self.attempt==1,'Resume/retry must reconcile, never submit')
  r=checkpoint['operation'];p=r['plan'];phase=int(r['state'][5]);kind=r['state'].split('_')[1];request=r['history'][-1]['request']
  self.main_and_actor();self.observation(p,'baseline' if phase==1 else 'phase1','old')
  # Revalidate immutable content and live controls immediately before any mutation.
  self.immutable_objects(p,phase,checkpoint['template_receipts'][f'phase{phase}'])
  controls=checkpoint['controls'];d.need(digest(controls)==p['review']['controls_sha256'],'Controls differ from release review')
  policy=json.loads(self.t.aws('s3api','get-bucket-policy',bucket=d.BUCKET,expected_bucket_owner=d.ACCOUNT)['Policy']);d.need(digest(policy)==controls['asset_policy_sha256'],'Asset policy changed')
  d.need(self.t.aws('s3api','get-bucket-versioning',bucket=d.BUCKET,expected_bucket_owner=d.ACCOUNT).get('Status')=='Enabled','Versioning changed')
  try:life=self.t.aws('s3api','get-bucket-lifecycle-configuration',bucket=d.BUCKET,expected_bucket_owner=d.ACCOUNT);d.need(not life.get('Rules'),'Unexpected expiration')
  except subprocess.CalledProcessError as error:d.need('(NoSuchLifecycleConfiguration)' in (error.stderr or ''),'Lifecycle absence unverified')
  existing=self.describe(request['ChangeSetName'])
  if kind=='create':
   d.need(existing is None,'Change set already exists; reconcile without retry')
   kw={ 'stack_name':request['StackName'],'change_set_name':request['ChangeSetName'],'change_set_type':'UPDATE','role_arn':request['RoleARN'],'template_url':request['TemplateURL'],'client_token':request['ClientToken'],'description':request['Description'],'capabilities':request['Capabilities']}
   return self.t.aws('cloudformation','create-change-set',**kw)
  d.need(existing is not None,'Reviewed change set disappeared')
  existing=self.verified_change_set(r,phase)
  j.validate_changes(r[f'phase{phase}_change_set'],p,phase,self.cs_template(existing['ChangeSetId']),existing)
  d.need(existing==r[f'phase{phase}_change_set'],'Prepared change-set receipt changed')
  self.validation_gate(existing)
  return self.t.aws('cloudformation','execute-change-set',stack_name=d.STACK,change_set_name=request['ChangeSetName'],client_request_token=request['ClientRequestToken'])

def write_json(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(d.canonical(value));path.chmod(0o600)
def dispatch():
 c=d.java_active();d.need(c.get('java_release_enabled') is True,'Ordinary release adapter disabled')
 d.need(c.get('private_checkpoint_upload_approved') is True and c.get('public_checkpoint_reference_sharing_approved') is True,'Private persistence/public-reference disclosure not approved')
 d.need(os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_REF')=='refs/heads/main' and os.environ.get('GITHUB_WORKFLOW_REF')==REPO+'/'+WORKFLOW+'@refs/heads/main','Wrong workflow context')
 d.need(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch','Authenticated manual dispatch required')
 event=d.load(os.environ['GITHUB_EVENT_PATH']);ids=c.get('java_operator_actor_ids',[]) if event['inputs']['mode']=='operator-reconcile' else c.get('java_release_actor_ids',[])
 d.need(event.get('sender',{}).get('id') in ids and int(os.environ.get('GITHUB_ACTOR_ID','0')) in ids,'Unapproved actor')
 pins=json.loads(event['inputs']['review_pins']);source=os.environ['GITHUB_SHA'];d.need(pins['source_commit']==source,'Dispatch source mismatch')
 d.source_gate()
 extra=json.loads(event['inputs'].get('recovery') or '{}');d.need(set(extra)<= {'response_artifact_id','response_artifact_digest','operator_artifact_id','operator_artifact_digest','operator_private_ref'},'Unexpected recovery fields')
 return c,{**event['inputs'],**extra},Adapter(Transport(),c.get('java_release_actor_ids',[]),source,pins,os.environ['GITHUB_RUN_ID'],os.environ.get('GITHUB_RUN_ATTEMPT','1'),c.get('java_operator_actor_ids',[]))
def deployment_for_run(a):
 deployments=a.t.github(f'repos/{REPO}/deployments?sha={a.source}&environment=production&per_page=100')
 d.need(len(deployments)<100,'Deployment discovery incomplete');matches=[]
 for x in deployments:
  statuses=a.t.github(f'repos/{REPO}/deployments/{x["id"]}/statuses?per_page=100');url=f'https://github.com/{REPO}/actions/runs/{a.run_id}'
  if any(s.get('log_url')==url or s.get('log_url','').startswith(url+'/job/') for s in statuses):matches.append(x['id'])
 d.need(len(matches)==1,'Production deployment identity is not uniquely verified');return matches[0]
def bounded_reconciliation_wait(a,checkpoint):
 import time
 request=checkpoint['operation']['history'][-1]['request'];kind=checkpoint['operation']['state'].split('_')[1];deadline=time.monotonic()+600
 while True:
  cs=a.describe(request['ChangeSetName'])
  d.need(cs is not None,'Uncertain request absent; stop, never automatically resubmit')
  pending=cs.get('Status') in ['CREATE_PENDING','CREATE_IN_PROGRESS'] if kind=='create' else cs.get('ExecutionStatus')=='EXECUTE_IN_PROGRESS'
  if not pending:return
  d.need(time.monotonic()<deadline,'Reconciliation still pending after ten minutes');time.sleep(5)
def output(values):
 if os.environ.get('GITHUB_OUTPUT'):
  with open(os.environ['GITHUB_OUTPUT'],'a') as f:
   for k,v in values.items():f.write(f'{k}={v}\n')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['gate','review','stage','submit','operator-reconcile']);ap.add_argument('--out',type=Path,default=Path('java-receipts'));args=ap.parse_args()
 c,inputs,a=dispatch()
 if args.mode=='gate':return
 args.out.mkdir(parents=True,exist_ok=True)
 if args.mode=='review':
  d.need(inputs['mode']=='review','Wrong dispatch mode');bundle=a.t.private_record(private.validate_reference(json.loads(inputs['bundle_ref'])))
  review=bundle['review'];d.need(review.get('source_commit') in [None,a.source],'Stored review source conflicts');review['source_commit']=a.source
  import source_promotion
  local_source=json.loads(Path('.private/cdk-source/bible-verse-stack.template.json').read_text())
  source_promotion.review_bundle(bundle,local_source,Path('target/bibleverse-2.0.0-aws.jar'))
  plan=j.derive(bundle['baseline'],bundle['candidate'],bundle['assets'],review)
  d.need(pinset(plan)==a.pins,'Review does not match authenticated dispatch pins')
  deployment=deployment_for_run(a);a.provenance(a.run_id,deployment)
  controls=bundle['controls'];d.need(digest(controls)==review['controls_sha256'],'Unreviewed controls')
  from datetime import timedelta
  record={'kind':'java-review','plan':plan,'candidate':bundle['candidate'],'controls':controls,'template_receipts':bundle['template_receipts'],'plan_sha256':digest(plan),'source_commit':a.source,'run_id':a.run_id,'deployment_id':deployment,'expires_at':(a.t.now()+timedelta(days=3)).isoformat()}
  write_json(args.out/'review.json',record);output({'artifact_name':'bible-java-review-'+digest(plan)});return
 review=a.read_artifact(inputs['review_artifact_id'],inputs['review_artifact_digest'],'java-review')
 if args.mode=='operator-reconcile':
  d.need(inputs['mode']=='operator-reconcile','Wrong dispatch mode')
  checkpoint=a.read_artifact(inputs['resume_artifact_id'],inputs['resume_artifact_digest'],'java-intent');r=checkpoint['operation'];phase=int(r['state'][5]);d.need(r['state']==f'phase{phase}_create_intent','Only uncertain creation attribution may be reconciled here')
  bundle=a.t.private_record(private.validate_reference(inputs['operator_private_ref']));event=bundle['event'];d.need(digest(event)==bundle['event_sha256'],'Operator event does not match independently supplied digest');event=private.bounded_operator_event(event)
  cs=a.describe(r['history'][-1]['request']['ChangeSetName']);d.need(cs is not None,'Create absent; no replay authorization');a.operator_event(r,phase,event,cs)
  deployment=deployment_for_run(a);a.provenance(a.run_id,deployment,operator=True)
  record={'kind':'java-operator-evidence','source_commit':a.source,'plan_sha256':a.pins['plan_sha256'],'run_id':a.run_id,'deployment_id':deployment,'intent_artifact_id':int(inputs['resume_artifact_id']),'intent_artifact_digest':inputs['resume_artifact_digest'],'request_sha256':digest(r['history'][-1]['request']),'intent_state':r['state'],'event':event}
  write_json(args.out/'operator-evidence.json',record);output({'artifact_name':'bible-java-operator-'+a.pins['plan_sha256']+'-'+r['state']});return
 d.need(inputs['mode']=='release','Wrong release mode')
 if args.mode=='stage':
  resume_id=os.environ.get('RESUME_ARTIFACT_ID') or inputs.get('resume_artifact_id')
  resume_digest=os.environ.get('RESUME_ARTIFACT_DIGEST') or inputs.get('resume_artifact_digest')
  resume=a.read_artifact(resume_id,resume_digest,'java-intent') if resume_id else None
  if resume:
   d.need(resume['operation']['plan']==review['plan'],'Checkpoint and approved review differ');bounded_reconciliation_wait(a,resume)
  response_id=os.environ.get('RESPONSE_ARTIFACT_ID') or inputs.get('response_artifact_id');response_digest=os.environ.get('RESPONSE_ARTIFACT_DIGEST') or inputs.get('response_artifact_digest')
  response=a.read_artifact(response_id,response_digest,'java-response') if response_id else None
  operator=a.read_artifact(inputs['operator_artifact_id'],inputs['operator_artifact_digest'],'java-operator-evidence') if inputs.get('operator_artifact_id') else None
  operation=a.next_intent(review,args.out/'local-operation.json',review['template_receipts'],resume,response,operator)
  if operation is None:output({'complete':'true'});write_json(args.out/'complete.json',{'plan_sha256':a.pins['plan_sha256'],'complete':True});return
  deployment=deployment_for_run(a);a.provenance(a.run_id,deployment)
  operation.setdefault('intent_origins',{})[operation['state']]={'run_id':a.run_id,'created_at':a.t.now().isoformat()}
  checkpoint={'kind':'java-intent','source_commit':a.source,'plan_sha256':a.pins['plan_sha256'],'run_id':a.run_id,'run_attempt':a.attempt,'deployment_id':deployment,'operation':operation,'request_sha256':digest(operation['history'][-1]['request']),'template_receipts':review['template_receipts'],'controls':review['controls']}
  write_json(args.out/'operation.json',checkpoint);output({'complete':'false','artifact_name':'bible-java-intent-'+operation['plan_digest']+'-'+operation['state']});return
 checkpoint=a.read_artifact(os.environ['INTENT_ARTIFACT_ID'],os.environ['INTENT_ARTIFACT_DIGEST'],'java-intent')
 d.need(checkpoint['operation']['plan']==review['plan'] and checkpoint['controls']==review['controls'] and checkpoint['template_receipts']==review['template_receipts'],'Submitted checkpoint differs from review')
 # Exclusive same-run gate; rerun attempts are rejected by submit, and later runs must reconcile.
 d.exclusive_intent(args.out/'submission-gate.json',{'artifact_id':os.environ['INTENT_ARTIFACT_ID'],'digest':os.environ['INTENT_ARTIFACT_DIGEST']})
 result=a.submit(checkpoint)
 receipt={'kind':'java-response','source_commit':a.source,'plan_sha256':a.pins['plan_sha256'],'run_id':a.run_id,'deployment_id':checkpoint['deployment_id'],'intent_artifact_id':int(os.environ['INTENT_ARTIFACT_ID']),'intent_artifact_digest':os.environ['INTENT_ARTIFACT_DIGEST'],'intent_state':checkpoint['operation']['state'],'request_sha256':checkpoint['request_sha256'],'response':result}
 write_json(args.out/'response.json',receipt);output({'artifact_name':'bible-java-response-'+a.pins['plan_sha256']+'-'+checkpoint['operation']['state']})
if __name__=='__main__':main()
