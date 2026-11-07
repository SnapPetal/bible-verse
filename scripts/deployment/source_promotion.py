"""Fail-closed legacy-CDK to app-only promotion; local pure functions, no deployment.
Review hashes must come from independent review ingestion, not this module.
"""
import copy,json,hashlib,re
from pathlib import Path
import java_release as j
from bible_deploy import need,canonical

def sha(x):return hashlib.sha256(canonical(x)).hexdigest()
def pin(value,expected,label):need(isinstance(expected,str) and sha(value)==expected,label+' differs from independent review')
def normalize_app_asset_metadata(resource,source):
 metadata=resource.get('Metadata',{})
 if 'aws:asset:path' not in metadata:return
 path=metadata['aws:asset:path']
 need(isinstance(path,str) and re.fullmatch(r'asset\.[a-f0-9]{64}\.jar',path),'Unexpected app asset metadata path')
 need(metadata.get('aws:asset:property')=='Code' and metadata.get('aws:asset:is-bundled') is False,'Unexpected app asset metadata configuration')
 if source:need(path=='asset.'+resource['Properties']['Code']['S3Key'],'Source asset metadata differs from Code')
 # The independently pinned live template retains its original inert CDK asset
 # metadata after promotion; only this bounded hash-path field is normalized.
 metadata['aws:asset:path']={'PROMOTION_MASK':'app-asset-metadata-only'}
def split(t):
 x=copy.deepcopy(t);versions={}
 ids={j.version_ref(x['Resources'][info['alias']]):fid for fid,info in j.FUNCTIONS.items()}
 need(len(ids)==2,'Two distinct current versions required')
 need({k for k,v in x['Resources'].items() if v['Type']=='AWS::Lambda::Version'}==set(ids),'Unexpected managed Version declaration')
 for vid,fid in ids.items():
  v=x['Resources'].pop(vid)
  need(v['Properties']=={'FunctionName':{'Ref':fid}},'Unexpected source version properties')
  need(v.get('DeletionPolicy')=='Retain' and v.get('UpdateReplacePolicy')=='Retain','Source version retention missing')
  versions[fid]={'id':vid,'resource':v}
  normalize_app_asset_metadata(x['Resources'][fid],True)
  x['Resources'][fid]['Properties']['Code']={'PROMOTION_MASK':'app-code-only'}
  x['Resources'][j.FUNCTIONS[fid]['alias']]['Properties']['FunctionVersion']={'PROMOTION_MASK':'app-version-only'}
 return x,versions

def bridge(source,live):
 """Only reviewed preserved CDKMetadata and promoted app fields may differ."""
 projection,versions=split(source);x=copy.deepcopy(live)
 if 'CDKMetadata' in x['Resources'] and 'CDKMetadata' not in source['Resources']:
  need(x['Resources']['CDKMetadata']['Type']=='AWS::CDK::Metadata','Unexpected extra resource')
  x['Resources'].pop('CDKMetadata')
 for fid,info in j.FUNCTIONS.items():
  vid=j.version_ref(x['Resources'][info['alias']]);v=x['Resources'].pop(vid)
  need(set(v['Properties'])<= {'FunctionName','CodeSha256'} and v['Properties']['FunctionName']=={'Ref':fid},'Unexpected live version configuration')
  comparable=copy.deepcopy(v);comparable['Properties'].pop('CodeSha256',None)
  need(comparable==versions[fid]['resource'],'Source/live version lifecycle or dependency differs')
  normalize_app_asset_metadata(x['Resources'][fid],False)
  x['Resources'][fid]['Properties']['Code']={'PROMOTION_MASK':'app-code-only'}
  x['Resources'][info['alias']]['Properties']['FunctionVersion']={'PROMOTION_MASK':'app-version-only'}
 need(x==projection,'Unsupported source/private baseline difference outside CDKMetadata and promoted app fields')

def promote(source_baseline,source_current,live_baseline,assets,review):
 need(review.get('enabled') is False and review.get('mode') in ['identical-cutover','ordinary-java'],'Wrong review mode/activation')
 for value,key in [(source_baseline,'source_baseline_sha256'),(source_current,'source_candidate_sha256'),(live_baseline,'live_baseline_sha256'),(assets,'assets_sha256')]:pin(value,review.get(key),key)
 need(set(assets)==set(j.FUNCTIONS),'Wrong assets')
 bridge(source_baseline,live_baseline)
 old_projection,old_versions=split(source_baseline);new_projection,new_versions=split(source_current)
 need(old_projection==new_projection,'Unsupported source infrastructure/config/helper/data/retention change')
 for fid in j.FUNCTIONS:
  old=old_versions[fid]['resource'];new=new_versions[fid]['resource']
  need(old==new,'Unsupported version declaration change')
  code=source_current['Resources'][fid]['Properties']['Code']
  need(set(code)=={'S3Bucket','S3Key'},'Expected legacy CDK file-asset reference')
  need(code==review['source_code_refs'][fid],'Source asset identity differs')
 out=copy.deepcopy(live_baseline)
 for fid,info in j.FUNCTIONS.items():
  a=assets[fid];code={'S3Bucket':j.BUCKET,'S3Key':a['key'],'S3ObjectVersion':a['version_id']};j.artifact(a,code)
  out['Resources'][fid]['Properties']['Code']=code
  old_live=j.version_ref(live_baseline['Resources'][info['alias']]);old_source=old_versions[fid]['id'];new_source=new_versions[fid]['id']
  if review['mode']=='identical-cutover':
   need(source_current==source_baseline,'Initial cutover must preserve complete source synthesis')
   need(a['sha256_base64']==review['old_code_sha256'][fid],'Initial cutover bytes differ')
  else:
   need(new_source!=old_source and new_source not in live_baseline['Resources'],'Ordinary release must generate new CDK version IDs')
   need(a['sha256_base64']!=review['old_code_sha256'][fid],'Ordinary release must change JAR bytes')
   old=live_baseline['Resources'][old_live]
   need(old.get('DeletionPolicy')=='Retain' and old.get('UpdateReplacePolicy')=='Retain','Live rollback version not retained')
   new=copy.deepcopy(old);new['Properties']={'FunctionName':{'Ref':fid},'CodeSha256':a['sha256_base64']}
   out['Resources'].pop(old_live);out['Resources'][new_source]=new
   out['Resources'][info['alias']]['Properties']['FunctionVersion']={'Fn::GetAtt':[new_source,'Version']}
 pin(out,review.get('promoted_candidate_sha256'),'Promoted output')
 return out

def review_bundle(bundle,local_source,jar_path):
 """No AWS: bind actual runner synth/JAR to the externally reviewed private bundle."""
 need(isinstance(bundle.get('source_promotion'),dict),'Missing source promotion review')
 sp=bundle['source_promotion'];pin(sp,bundle['review'].get('source_promotion_sha256'),'Source promotion bundle')
 need(set(sp)=={'source_baseline','source_current','review'},'Unexpected source promotion schema')
 need(sp['review'].get('mode')=='ordinary-java','Java review requires ordinary promotion')
 need(local_source==sp['source_current'],'Actual runner synthesis differs from reviewed source')
 raw=Path(jar_path).read_bytes();import base64
 hashed=base64.b64encode(hashlib.sha256(raw).digest()).decode()
 for a in bundle['assets'].values():
  need(a['sha256_base64']==hashed and a['bytes']==len(raw),'Built JAR differs from immutable receipt')
 result=promote(sp['source_baseline'],local_source,bundle['baseline'],bundle['assets'],sp['review'])
 need(result==bundle['candidate'],'Promotion differs from release candidate')
 return result
