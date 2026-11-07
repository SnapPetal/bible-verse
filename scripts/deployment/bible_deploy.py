#!/usr/bin/env python3
"""Bible-only release client. No CDK deployment; every mutating path is locked by default."""
import creation_evidence as ce
import argparse, base64, hashlib, json, os, re, subprocess, tempfile, time
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[2]
ACCOUNT='664759038511'; REGION='us-east-1'
BUCKET='bible-verse-cdk-assets-664759038511-us-east-1'
STACK='arn:aws:cloudformation:us-east-1:664759038511:stack/bible-verse-stack/fa26c080-376d-11ed-b814-122748dfbf1b'
EXECUTOR=f'arn:aws:iam::{ACCOUNT}:role/BibleVerseCfnExecution'
AUTO='BibleVerseBucketAutoDeleteObjectsCustomResource64B22329'
CONFIG=ROOT/'.infrastructure/deployment/activation.json'
def need(ok,msg):
 if not ok: raise ValueError(msg)
def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def sha(x): return hashlib.sha256(x).hexdigest()
def load(p): return json.loads(Path(p).read_text())
def save(p,x):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(canonical(x)); p.chmod(0o600)
def aws(service,operation,**kw):
 args=['aws',service,operation,'--region',REGION,'--output','json','--no-cli-pager']
 for k,v in kw.items():
  if isinstance(v,bool):
   need(v,'False flags must be omitted'); args+=['--'+k.replace('_','-')]
  else: args+=['--'+k.replace('_','-'),json.dumps(v) if isinstance(v,(dict,list)) else str(v)]
 env={**os.environ,'AWS_MAX_ATTEMPTS':'1','AWS_RETRY_MODE':'standard'}
 r=subprocess.run(args,env=env,check=True,capture_output=True,text=True)
 return json.loads(r.stdout) if r.stdout.strip() else {}
def source_gate():
 need(os.environ.get('GITHUB_REPOSITORY')=='SnapPetal/bible-verse','Wrong repository')
 need(os.environ.get('GITHUB_REF')=='refs/heads/main','Only main dispatch permitted')
 commit=os.environ.get('GITHUB_SHA',''); need(re.fullmatch('[0-9a-f]{40}',commit),'Invalid source SHA')
 head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 remote=subprocess.check_output(['git','ls-remote','--exit-code','origin','refs/heads/main'],cwd=ROOT,text=True).split()
 need(head==commit and remote==[commit,'refs/heads/main'],'Source no longer current main')
 return commit
def actor(role):
 x=aws('sts','get-caller-identity')
 need(x.get('Account')==ACCOUNT and x.get('Arn','').startswith(f'arn:aws:sts::{ACCOUNT}:assumed-role/{role}/'),'Wrong AWS actor')
 return {'Account':x['Account'],'Arn':x['Arn']}
def exclusive_intent(path,value):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:
  json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
 directory=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(directory)
 finally:os.close(directory)
def verify_auth(out):
 c=active();source_gate();identity=actor('BibleVerseCdkDeploy')
 # This verifies the post-cutover path even if a stale migration-mode config remains.
 c={**c,'mode':'steady-state'};t=baseline(c)
 save(out/'auth-verification.json',{'identity':identity,'stack':STACK,'executor':EXECUTOR,'template_sha256':sha(canonical(t)),'read_only':True})

def active():
 c=load(CONFIG); need(c.get('enabled') is True,'Mutation disabled: activation approval incomplete')
 for name in ['retention_applied','data_recovery_verified','runtime_boundaries_verified','entry_replaced','executor_permissions_approved','asset_policy_verified']:
  need(c.get(name) is True,'Unaccepted prerequisite: '+name)
 need(c.get('mode') in ['one-time-byte-preserving','steady-state'],'Unknown release mode')
 for name in ['baseline_sha256','candidate_sha256','asset_policy_sha256']:
  need(re.fullmatch('[0-9a-f]{64}',c.get(name,'')),'Missing digest: '+name)
 return c

def java_active():
 # Only ordinary two-app code/version/alias releases. Never enables migration/helper paths.
 c=load(CONFIG)
 need(c.get('enabled') is True and c.get('java_release_enabled') is True,'Two-app release activation disabled')
 need(c.get('mode')=='two-app-java' and c.get('migration_adapter_enabled') is False,'Wrong two-app mode or migration enabled')
 need(c.get('baseline_executor')==EXECUTOR,'Dedicated app executor not pinned')
 for name in ['retention_applied','entry_replaced','executor_permissions_approved','asset_policy_verified']:
  need(c.get(name) is True,'Unaccepted two-app prerequisite: '+name)
 need(re.fullmatch('[a-f0-9]{64}',c.get('asset_policy_sha256','')),'Missing private asset policy pin')
 import executor_policy_pair
 executor_policy_pair.verify_activation(c)
 # Per-release source/template/asset/stage pins and actor identity are checked by
 # authenticated dispatch, derive(), live observations and exact change-set gates.
 # No assertion about attached boundaries or helper/data recovery is made here.
 return c

def object_receipt(h,a):
 need(h.get('ServerSideEncryption')=='AES256' and h.get('ContentLength')==a['bytes'],'Object size/encryption mismatch')
 need(h.get('ChecksumSHA256')==a['sha256_base64'] and h.get('ChecksumType','FULL_OBJECT')=='FULL_OBJECT','Object checksum mismatch')
 need(h.get('VersionId') not in [None,'','null'],'Missing immutable version')
 return {'bucket':BUCKET,'key':a['key'],'version_id':h['VersionId'],'sha256_base64':a['sha256_base64'],'bytes':a['bytes']}
def head(a,version=None):
 kw=dict(bucket=BUCKET,key=a['key'],expected_bucket_owner=ACCOUNT,checksum_mode='ENABLED')
 if version: kw['version_id']=version
 return aws('s3api','head-object',**kw)
def publish_one(path,a):
 """Exactly one conditional PUT; any ambiguous/error outcome stops for read-only reconciliation."""
 data=Path(path).read_bytes()
 need(len(data)==a['bytes'] and base64.b64encode(hashlib.sha256(data).digest()).decode()==a['sha256_base64'],'Local payload changed')
 need(re.fullmatch(r'(?:assets/[a-f0-9]{64}\.(?:jar|zip)|reviews/[a-f0-9]{64}\.json)',a['key']),'Unreviewed key')
 if a.get('version_id'):
  return object_receipt(head(a,a['version_id']),a)
 # Freeze bytes against local path races. No multipart, overwrite, copy, delete or automatic retry.
 with tempfile.TemporaryDirectory() as td:
  frozen=Path(td)/'payload'; frozen.write_bytes(data)
  result=aws('s3api','put-object',bucket=BUCKET,key=a['key'],body=str(frozen),expected_bucket_owner=ACCOUNT,server_side_encryption='AES256',storage_class='STANDARD',checksum_algorithm='SHA256',checksum_sha256=a['sha256_base64'],if_none_match='*')
 need(result.get('VersionId') not in [None,'','null'],'Uncertain publication; reconcile, do not retry')
 receipt=object_receipt(head(a,result['VersionId']),a)
 need(receipt['version_id']==result['VersionId'],'Wrong object version')
 return receipt

def publish_reviewed(manifest_path,out):
 c=active(); source_gate(); p=load(manifest_path)
 need(sha(canonical(p))==c.get('publication_manifest_sha256'),'Unapproved publication manifest')
 actor=aws('sts','get-caller-identity')
 need(actor.get('Account')==ACCOUNT and actor.get('Arn','').startswith(f'arn:aws:sts::{ACCOUNT}:assumed-role/BibleVerseCdkFilePublisher/'),'Wrong publisher identity')
 receipts=[]
 for a in p['assets']:
  path=(ROOT/a['file']).resolve(); need(path.is_relative_to(ROOT.resolve()),'Payload path escapes source')
  intent_path=out/('publish-'+sha(a['key'].encode())+'.intent.json')
  try:exclusive_intent(intent_path,{'asset':a,'manifest_sha256':c['publication_manifest_sha256']})
  except FileExistsError:
   save(out/'publication-reconciliation.json',object_receipt(head(a,a.get('version_id')),a))
   raise ValueError('Existing publication intent; reconcile receipt before any new approval/retry')
  receipts.append(publish_one(path,a)); save(out/'publication-receipts.json',receipts)

def original(response):
 t=response['TemplateBody']; return json.loads(t) if isinstance(t,str) else t

def baseline(c):
 s=aws('cloudformation','describe-stacks',stack_name=STACK)['Stacks'][0]
 need(s['StackId']==STACK and s['StackStatus']=='UPDATE_COMPLETE','Not exact stable app stack')
 # First migration explicitly pins old executor. Ordinary releases require dedicated executor.
 expected=c.get('baseline_executor') if c['mode']=='one-time-byte-preserving' else EXECUTOR
 need(expected and s.get('RoleARN')==expected,'Stored executor differs')
 t=original(aws('cloudformation','get-template',stack_name=STACK,template_stage='Original'))
 need(sha(canonical(t))==c['baseline_sha256'],'Stack template drift')
 policy=json.loads(aws('s3api','get-bucket-policy',bucket=BUCKET,expected_bucket_owner=ACCOUNT)['Policy'])
 need(sha(canonical(policy))==c['asset_policy_sha256'],'Immutable bucket policy changed')
 need(aws('s3api','get-bucket-versioning',bucket=BUCKET,expected_bucket_owner=ACCOUNT).get('Status')=='Enabled','Versioning changed')
 try:
  rules=aws('s3api','get-bucket-lifecycle-configuration',bucket=BUCKET,expected_bucket_owner=ACCOUNT)
  need(not rules.get('Rules'),'Unexpected asset expiration')
 except subprocess.CalledProcessError as e:
  need('(NoSuchLifecycleConfiguration)' in (e.stderr or ''),'Cannot verify lifecycle absence')
 return t

def validate_candidate(t,b,c):
 need(sha(canonical(t))==c['candidate_sha256'],'Candidate not exact reviewed template')
 need(t['Resources'][AUTO]==b['Resources'][AUTO],'Auto-delete resource must not change at all')
 need(not t.get('Transform'),'Transforms are not approved')
 # Exact template digest, not a permissive type allowlist. Retention must already be live.
 for k,r in b['Resources'].items():
  if r['Type'] in ['AWS::Lambda::Version','AWS::Lambda::LayerVersion','AWS::S3::Bucket','Custom::S3AutoDeleteObjects']:
   need(r.get('DeletionPolicy')=='Retain' and r.get('UpdateReplacePolicy')=='Retain','Retention not applied: '+k)
 # Initial reference migration must stage versions first, preserving aliases; traffic shift is a separate approval.
 for k,r in t['Resources'].items():
  if r['Type']=='AWS::Lambda::Alias':
   need(r==b['Resources'].get(k),'Alias shift requires separate Active/On verification and approval; not supported by this unit')
 # No generic future-release allowance: each future template needs an explicit digest review.
 for k,r in t['Resources'].items():
  if r['Type'].startswith('AWS::IAM::'):
   need(r==b['Resources'].get(k),'IAM edits are operator-only')
 for k,r in b['Resources'].items():
  if r['Type'].startswith('AWS::IAM::'): need(t['Resources'].get(k)==r,'IAM deletion is operator-only')


def describe(cs):
 x=aws('cloudformation','describe-change-set',stack_name=STACK,change_set_name=cs,include_property_values=True)
 need(not x.get('NextToken'),'Paginated review is incomplete; stop')
 return x

def check_changes(x,c,template,expected_arn=None):
 need(not x.get('NextToken'),'Incomplete paginated change set')
 arn=x.get('ChangeSetId','')
 need(re.fullmatch(r'arn:aws:cloudformation:us-east-1:664759038511:changeSet/bible-isolated-update-[a-zA-Z0-9-]+/[a-zA-Z0-9-]+',arn),'Wrong change-set ARN')
 if expected_arn: need(arn==expected_arn,'Change-set identity changed')
 need(x.get('StackId')==STACK and ce.executor_verified(x),'Wrong stack/executor')
 need(x.get('Status')=='CREATE_COMPLETE' and x.get('ExecutionStatus')=='AVAILABLE','Change set unavailable')
 need(not x.get('ParentChangeSetId') and not x.get('RootChangeSetId') and not x.get('IncludeNestedStacks'),'Nested change set forbidden')
 need(sha(canonical(template))==c['candidate_sha256'],'Change-set template differs')
 changes=x.get('Changes',[]); need(changes,'Empty change set')
 if 'baseline' in c:
  before=c['baseline']['Resources']; after=template['Resources']
  expected={k for k in set(before)|set(after) if before.get(k)!=after.get(k)}
  actual=[i['ResourceChange']['LogicalResourceId'] for i in changes]
  need(len(actual)==len(set(actual)) and set(actual)==expected,'Unexpected or incomplete resolved resource scope')
 for change in changes:
  r=change['ResourceChange']; need(r['LogicalResourceId']!=AUTO,'Auto-delete update forbidden')
  need(not r['ResourceType'].startswith('AWS::IAM::'),'IAM delta forbidden')
  need(r.get('Replacement') in (['False','True'] if r['ResourceType']=='AWS::Lambda::LayerVersion' else ['False']) if r['Action']=='Modify' else r.get('Replacement','False')=='False','Unresolved or unexpected replacement')
  need(r['Action']!='Remove' or r['ResourceType']=='AWS::Lambda::Version','Unexpected removal')
 return sha(canonical(changes))

def verify_references(t,assets):
 receipts={a['key']:a for a in assets}; need(len(receipts)==len(assets),'Duplicate asset receipts')
 used=set()
 for r in t['Resources'].values():
  props=r.get('Properties',{})
  if r['Type'] in ['AWS::Lambda::Function','AWS::Lambda::LayerVersion']:
   ref=props['Code' if r['Type']=='AWS::Lambda::Function' else 'Content']
   need(ref.get('S3Bucket')==BUCKET,'Shared/non-dedicated code source')
   key=ref.get('S3Key'); need(key in receipts,'Missing referenced code receipt')
   need(ref.get('S3ObjectVersion')==receipts[key]['version_id'],'Code must pin exact verified version')
   used.add(key)
  if r['Type']=='Custom::CDKBucketDeployment':
   keys=props.get('SourceObjectKeys',[])
   need(props.get('SourceBucketNames')==[BUCKET]*len(keys) and keys,'Non-dedicated data source')
   for key in keys:
    need(key in receipts,'Missing data receipt'); used.add(key)
    # Provider lacks a version selector: immutable key and current-version equality are essential.
    need(head(receipts[key])['VersionId']==receipts[key]['version_id'],'Data current version differs')
 need(used==set(receipts),'Missing or extra publication receipt')

def prepare(plan_path,out):
 raise ValueError('Direct migration writes are disabled; use the authenticated migration_adapter workflow')
def execute(receipt_path,out):
 raise ValueError('Direct migration writes are disabled; use the authenticated migration_adapter workflow')

def reconcile(cs,out):
 save(out/'stack.json',aws('cloudformation','describe-stacks',stack_name=STACK))
 save(out/'changes.json',describe(cs))
 save(out/'events.json',aws('cloudformation','describe-stack-events',stack_name=STACK))

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('mode',choices=['verify','verify-auth','identity-entry','publish','prepare','execute','reconcile']); ap.add_argument('--input',type=Path); ap.add_argument('--out',type=Path,default=Path('deployment-receipts')); ap.add_argument('--change-set'); a=ap.parse_args()
 if a.mode=='verify':
  need(isinstance(load(CONFIG).get('enabled'),bool),'Invalid activation state')
  import executor_policy_pair
  executor_policy_pair.verify_source_pair()
  print('Offline verification complete. No AWS calls made; activation state unchanged.')
 elif a.mode=='verify-auth':verify_auth(a.out)
 elif a.mode=='identity-entry':save(a.out/'entry-identity.json',actor('GitHubActionsBibleVerseProdDeploy'))
 elif a.mode=='publish': publish_reviewed(a.input,a.out)
 elif a.mode=='prepare': prepare(a.input,a.out)
 elif a.mode=='execute': execute(a.input,a.out)
 else: reconcile(a.change_set,a.out)
if __name__=='__main__': main()
