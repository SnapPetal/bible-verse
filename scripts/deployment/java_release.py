"""Offline two-phase ordinary Java release protocol. No AWS client or activation route.
Approval digests are INPUTS from a separate review, never calculated as authorization.
The caller must persist request intent before submitting it once, then reconcile.
"""
import creation_evidence as ce
import copy, fcntl, hashlib, json, os, re, tempfile
from pathlib import Path
from contextlib import contextmanager
from bible_deploy import ACCOUNT, REGION, STACK, EXECUTOR, BUCKET, canonical, sha, need
FUNCTIONS={
 'AboutFunctionAC87887C':{'name':'bible-verse-stack-AboutFunctionAC87887C-wAQ2Yfm0gK8F','alias':'AboutFunctionLive3BC9ADF2'},
 'RandomBibleVerseFunctionFDC072CB':{'name':'bible-verse-stack-RandomBibleVerseFunctionFDC072CB-gnvb3C8pZGqB','alias':'RandomBibleVerseLive169430C5'},
}
DEPLOY=f'arn:aws:iam::{ACCOUNT}:role/BibleVerseCdkDeploy'
def exact_digest(value,digest,label):
 need(isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) and sha(canonical(value))==digest,label+' not separately reviewed')
def version_ref(alias):
 x=alias['Properties']['FunctionVersion']; need(isinstance(x,dict) and set(x)=={'Fn::GetAtt'},'Alias must reference reviewed version resource')
 a=x['Fn::GetAtt']; need(isinstance(a,list) and len(a)==2 and a[1]=='Version','Unexpected alias target');return a[0]
def artifact(a,code):
 need(code=={'S3Bucket':BUCKET,'S3Key':a['key'],'S3ObjectVersion':a['version_id']},'Code/receipt mismatch')
 need(a.get('bucket')==BUCKET and a.get('owner')==ACCOUNT and a.get('encryption')=='AES256','Wrong asset destination/encryption')
 need(isinstance(a.get('version_id'),str) and a['version_id'] not in ['','null'],'Unversioned artifact')
 need(re.fullmatch('assets/[a-f0-9]{64}\\.jar',a.get('key','')),'Unexpected app key')
 need(isinstance(a.get('bytes'),int) and a['bytes']>0,'Invalid artifact size')
 import base64
 try: raw=base64.b64decode(a['sha256_base64'],validate=True)
 except Exception as e: raise ValueError('Invalid SHA256') from e
 need(len(raw)==32 and a.get('checksum_type')=='FULL_OBJECT','Invalid artifact checksum')

def derive(b,c,assets,review):
 """Validate exact normal CDK candidate; derive two stages without executing helpers."""
 need(review.get('kind')=='ordinary-java-two-phase' and review.get('activation') is False,'Wrong review kind/activation')
 exact_digest(b,review.get('baseline_sha256'),'Baseline');exact_digest(c,review.get('candidate_sha256'),'Candidate');exact_digest(assets,review.get('assets_sha256'),'Assets')
 need(re.fullmatch('[a-f0-9]{40}',review.get('source_commit','')),'Missing source pin')
 need(review.get('capabilities')==['CAPABILITY_NAMED_IAM'],'Capabilities must be explicitly reviewed for unchanged named IAM declarations')
 need(set(assets)==set(FUNCTIONS),'Wrong function asset set')
 s1=copy.deepcopy(b); expected=copy.deepcopy(b); versions={}
 for fid,info in FUNCTIONS.items():
  oldid=version_ref(b['Resources'][info['alias']]); newid=version_ref(c['Resources'][info['alias']])
  need(newid!=oldid and newid not in b['Resources'],'Version must be new')
  old_review=review['old_versions'][fid];old_number=old_review.get('version','')
  need(re.fullmatch('[1-9][0-9]*',old_number),'Missing reviewed old numeric version')
  arn=f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{info["name"]}'
  need(old_review.get('logical_id')==oldid and old_review.get('physical_id')==arn+':'+old_number,'Reviewed old version mapping differs')
  need(oldid not in c['Resources'],'Canonical candidate must omit exact old version declaration')
  old=b['Resources'][oldid]; new=c['Resources'][newid]
  need(old.get('DeletionPolicy')=='Retain' and old.get('UpdateReplacePolicy')=='Retain','Rollback version not retained')
  need(old['Type']=='AWS::Lambda::Version' and old['Properties'].get('FunctionName')=={'Ref':fid},'Wrong old function')
  need(new['Type']=='AWS::Lambda::Version' and new['Properties'].get('FunctionName')=={'Ref':fid},'Wrong new function')
  need(set(new['Properties'])<= {'FunctionName','CodeSha256'},'Unexpected version configuration')
  need(new.get('DeletionPolicy')=='Retain' and new.get('UpdateReplacePolicy')=='Retain','New version must be retained')
  need({k:v for k,v in new.items() if k!='Properties'}=={k:v for k,v in old.items() if k!='Properties'},'Version metadata/dependencies changed')
  code=c['Resources'][fid]['Properties']['Code'];artifact(assets[fid],code)
  need(new['Properties'].get('CodeSha256')==assets[fid]['sha256_base64'],'Canonical candidate must pin published version code hash')
  need(code!=b['Resources'][fid]['Properties']['Code'],'No code change')
  expected['Resources'][fid]['Properties']['Code']=copy.deepcopy(code)
  expected['Resources'][newid]=copy.deepcopy(new)
  if oldid not in c['Resources']:del expected['Resources'][oldid]
  alias=copy.deepcopy(b['Resources'][info['alias']]); alias['Properties']['FunctionVersion']={'Fn::GetAtt':[newid,'Version']}
  expected['Resources'][info['alias']]=alias
  s1['Resources'][fid]['Properties']['Code']=copy.deepcopy(code)
  s1['Resources'][newid]=copy.deepcopy(new)
  # Guard against Lambda publishing stale $LATEST after an out-of-band code change.
  s1['Resources'][newid]['Properties']['CodeSha256']=assets[fid]['sha256_base64']
  versions[fid]={'old_logical':oldid,'new_logical':newid,'old_version':old_number}
 need(c==expected,'Candidate includes helper/data/IAM/API/DNS/config or other unapproved delta')
 # Return to the exact reviewed canonical candidate: remove only old retained declarations.
 s2=copy.deepcopy(c)
 exact_digest(s1,review.get('phase1_sha256'),'Phase1');exact_digest(s2,review.get('phase2_sha256'),'Phase2')
 return {'review':review,'baseline':b,'phase1':s1,'phase2':s2,'assets':assets,'versions':versions}

def identity(e,source):
 need(e.get('account')==ACCOUNT and e.get('region')==REGION,'Wrong account/region')
 need(e.get('caller_role')==DEPLOY and e.get('stack_id')==STACK and e.get('executor')==EXECUTOR,'Wrong caller/stack/executor')
 need(e.get('current_main')==source and e.get('checked_out')==source,'Main/source changed')

def runtime(e,plan,new=False):
 """Require projected GetFunctionConfiguration + GetAlias + CFN physical-ID observations."""
 need(set(e['functions'])==set(FUNCTIONS),'Missing/extra runtime observations')
 for fid,info in FUNCTIONS.items():
  x=e['functions'][fid]; name=info['name']; arn=f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{name}'
  old=x['old'];old_expected=plan['review']['old_versions'][fid];old_number=old_expected['version']
  need(x.get('old_logical_id')==old_expected['logical_id'] and x.get('old_physical_id')==old_expected['physical_id'],'Old CFN logical/physical mapping differs')
  need(old.get('FunctionArn')==arn+':'+old_number and old.get('Version')==old_number,'Wrong rollback version/function')
  need(old.get('CodeSha256')==old_expected['code_sha256'] and old.get('State')=='Active' and old.get('SnapStart',{}).get('OptimizationStatus')=='On','Rollback version not ready')
  need(old.get('Role')==old_expected['role_arn'],'Rollback role changed')
  need(x['alias'].get('AliasArn')==arn+':live' and not x['alias'].get('RoutingConfig',{}).get('AdditionalVersionWeights'),'Wrong/weighted alias')
  if new:
   n=x['new'];v=n.get('Version','')
   need(re.fullmatch('[1-9][0-9]*',v) and int(v)>int(old_number),'Invalid published version')
   need(x['new_physical_id']==arn+':'+v and x['new_logical_id']==plan['versions'][fid]['new_logical'],'CFN version receipt differs')
   need(n.get('FunctionArn')==arn+':'+v and n.get('CodeSha256')==plan['assets'][fid]['sha256_base64'],'Wrong published function/code')
   need(n.get('State')=='Active' and n.get('SnapStart',{}).get('OptimizationStatus')=='On','New snapshot not ready')
   need(n.get('Role')==old_expected['role_arn'],'New runtime role changed')

def snapshot(e,plan,phase,aliases):
 identity(e,plan['review']['source_commit'])
 need(e.get('stack_status')=='UPDATE_COMPLETE','Stack incomplete/failed')
 exact_digest(e['template'],sha(canonical(plan[phase])),'Applied template')
 runtime(e,plan,new=phase!='baseline')
 for fid,info in FUNCTIONS.items():
  need(e['functions'][fid].get('old_mapping_source')==('recorded-baseline' if phase=='phase2' else 'current-stack'),'Wrong old mapping evidence source')
  wanted=plan['review']['old_versions'][fid]['version'] if aliases=='old' else e['functions'][fid]['new']['Version']
  need(e['functions'][fid]['alias'].get('FunctionVersion')==wanted,'Alias baseline differs')

class Operation:
 """One durable locked record. No request can be emitted twice, including after crashes.
 An intent without a receipt is uncertain, never a reason to resubmit. External callers
 must also avoid parallel operator operations; this file is not a distributed AWS lock.
 """
 def __init__(self,path):self.path=Path(path)
 @contextmanager
 def locked(self):
  self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.path.with_suffix('.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   r=json.loads(self.path.read_text()) if self.path.exists() else None
   yield r
 def write(self,r):
  fd,tmp=tempfile.mkstemp(dir=self.path.parent,prefix='.release-')
  try:
   with os.fdopen(fd,'w') as f:json.dump(r,f,sort_keys=True);f.flush();os.fsync(f.fileno())
   os.replace(tmp,self.path)
   fd=os.open(self.path.parent,os.O_RDONLY)
   try:os.fsync(fd)
   finally:os.close(fd)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 def start(self,plan,e):
  snapshot(e,plan,'baseline','old')
  with self.locked() as r:
   need(r is None,'Operation already exists; reconcile')
   self.write({'state':'reviewed','plan':plan,'plan_digest':sha(canonical(plan)),'baseline_runtime':e['functions'],'history':[]})
 def request(self,phase,kind,e,template_asset=None):
  need(phase in [1,2] and kind in ['create','execute'],'Unknown operation')
  with self.locked() as r:
   need(r is not None,'Missing operation');p=r['plan'];need(sha(canonical(p))==r['plan_digest'],'Record changed')
   previous='reviewed' if phase==1 else 'phase1_ready'
   need(r['state']==(previous if kind=='create' else f'phase{phase}_prepared'),'Wrong/uncertain phase; reconcile, do not repeat')
   snapshot(e,p,'baseline' if phase==1 else 'phase1','old')
   if phase==2:
    for fid in FUNCTIONS:need(e['functions'][fid]['new']==r['phase1_verified_runtime'][fid]['new'] and e['functions'][fid]['new_physical_id']==r['phase1_verified_runtime'][fid]['new_physical_id'],'Previously verified version changed')
   template=p[f'phase{phase}']; token=sha(canonical([r['plan_digest'],phase,kind]))
   if kind=='create':
    a=template_asset; need(isinstance(a,dict),'Missing immutable template receipt')
    digest=sha(canonical(template));need(a.get('owner')==ACCOUNT and a.get('bucket')==BUCKET and a.get('key')==f'reviews/{digest}.json' and a.get('sha256')==digest and a.get('version_id') not in [None,'','null'],'Template receipt differs')
    import base64
    need(a.get('encryption')=='AES256' and a.get('bytes')==len(canonical(template)) and a.get('sha256_base64')==base64.b64encode(hashlib.sha256(canonical(template)).digest()).decode() and a.get('checksum_type')=='FULL_OBJECT','Template integrity receipt differs')
    from urllib.parse import quote
    req={'StackName':STACK,'ChangeSetName':f'bible-isolated-update-java-{token[:32]}','ChangeSetType':'UPDATE','Capabilities':p['review']['capabilities'],'RoleARN':EXECUTOR,'TemplateURL':f'https://{BUCKET}.s3.{REGION}.amazonaws.com/{a["key"]}?versionId={quote(a["version_id"],safe="")}','ClientToken':token,'Description':f'java-release:{r["plan_digest"]}:phase{phase}'}
   else:
    cs=r[f'phase{phase}_change_set'];validate_changes(cs,p,phase,e['change_set_template'],e['change_set'])
    need(e['change_set']==cs,'Change-set review changed')
    req={'StackName':STACK,'ChangeSetName':cs['ChangeSetId'],'ClientRequestToken':token}
   r['state']=f'phase{phase}_{kind}_intent';r['history'].append({'state':r['state'],'request':req});self.write(r)
   return req
 def reconcile_create(self,phase,cs,template):
  with self.locked() as r:
   need(r is not None and r['state']==f'phase{phase}_create_intent','Not awaiting create reconciliation')
   p=r['plan'];request=r['history'][-1]['request']
   need(cs.get('ChangeSetName')==request['ChangeSetName'] and cs.get('Description')==request['Description'],'Wrong create receipt')
   validate_changes(cs,p,phase,template,cs)
   r[f'phase{phase}_change_set']=cs;r['state']=f'phase{phase}_prepared';r['history'].append({'state':r['state']});self.write(r)
 def reconcile_execute(self,phase,e):
  with self.locked() as r:
   need(r is not None and r['state']==f'phase{phase}_execute_intent','Not awaiting execute reconciliation')
   cs=e['change_set'];need(cs.get('ChangeSetId')==r[f'phase{phase}_change_set']['ChangeSetId'] and cs.get('StackId')==STACK and executor_verified(cs) and cs.get('ExecutionStatus')=='EXECUTE_COMPLETE','Execution not complete')
   snapshot(e,r['plan'],f'phase{phase}','old' if phase==1 else 'new')
   if phase==2:
    for fid in FUNCTIONS:need(e['functions'][fid]['new']==r['phase1_verified_runtime'][fid]['new'] and e['functions'][fid]['new_physical_id']==r['phase1_verified_runtime'][fid]['new_physical_id'],'Verified release version changed')
   r['state']='phase1_ready' if phase==1 else 'complete';r[f'phase{phase}_verified_runtime']=e['functions'];r['history'].append({'state':r['state']});self.write(r)

def executor_verified(cs):
 return ce.executor_verified(cs)

def validate_changes(reviewed,plan,phase,template,actual):
 need(actual.get('ChangeSetId')==reviewed.get('ChangeSetId') and re.fullmatch(r'arn:aws:cloudformation:us-east-1:664759038511:changeSet/bible-isolated-update-java-[a-f0-9]{32}/[A-Za-z0-9-]+',actual.get('ChangeSetId','')),'Wrong change-set ARN')
 need(actual.get('StackId')==STACK and executor_verified(actual),'Wrong change-set stack/role')
 need(actual.get('Capabilities')==plan['review']['capabilities'],'Unexpected capabilities')
 need(actual.get('Status')=='CREATE_COMPLETE' and actual.get('ExecutionStatus')=='AVAILABLE','Change set not available')
 need(not any(actual.get(k) for k in ['NextToken','IncludeNestedStacks','RootChangeSetId','ParentChangeSetId']),'Incomplete/nested change set')
 exact_digest(template,plan['review'][f'phase{phase}_sha256'],'Change-set template')
 allowed={fid:('Modify','AWS::Lambda::Function') for fid in FUNCTIONS} if phase==1 else {v['alias']:('Modify','AWS::Lambda::Alias') for v in FUNCTIONS.values()}
 if phase==1:allowed.update({v['new_logical']:('Add','AWS::Lambda::Version') for v in plan['versions'].values()})
 else:allowed.update({v['old_logical']:('Remove','AWS::Lambda::Version') for v in plan['versions'].values()})
 changes=actual.get('Changes',[]);need(len(changes)==len(allowed),'Wrong change count')
 seen=set()
 for item in changes:
  x=item['ResourceChange'];k=x['LogicalResourceId'];need(k in allowed and k not in seen,'Unexpected/duplicate resource');seen.add(k)
  need((x['Action'],x['ResourceType'])==allowed[k] and (x.get('Replacement')=='False' if x['Action']=='Modify' else x.get('Replacement','False')=='False'),'Unexpected action/replacement')
  if phase==2 and x['Action']=='Remove':
   old=plan['phase1']['Resources'][k]
   need(old.get('DeletionPolicy')=='Retain' and old.get('UpdateReplacePolicy')=='Retain' and x.get('PolicyAction')=='Retain','Old physical version would not be retained')
  elif phase==2:need(set(x.get('Scope',[]))<= {'Properties'},'Unexpected alias scope')
 need(actual.get('Changes')==reviewed.get('Changes'),'Change-set changes differ')
