#!/usr/bin/env python3
"""Private S3 records; public GitHub artifacts contain only exact validated references.
No upload is enabled by default. This module never prints private record contents.
"""
import argparse,base64,hashlib,json,os,re,subprocess,tempfile
from pathlib import Path
import bible_deploy as d
SCHEMA='bible-private-record-ref-v1'
FIELDS={'schema','bucket','owner','key','version_id','bytes','sha256','sha256_base64','encryption'}
KINDS={'java-review','java-intent','java-response','java-operator-evidence','migration-intent','migration-response','migration-operator-evidence','migration-review'}
def sha(raw):return hashlib.sha256(raw).hexdigest()
def validate_reference(ref):
 d.need(isinstance(ref,dict) and set(ref)==FIELDS,'Public artifact must contain only the reviewed reference fields')
 d.need(ref['schema']==SCHEMA and ref['bucket']==d.BUCKET and ref['owner']==d.ACCOUNT and ref['encryption']=='AES256','Wrong private reference destination')
 d.need(isinstance(ref['sha256'],str) and re.fullmatch('[a-f0-9]{64}',ref['sha256']) and ref['key']=='reviews/'+ref['sha256']+'.json','Non-content-addressed private record')
 d.need(isinstance(ref['version_id'],str) and re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,1024}',ref['version_id']) and ref['version_id']!='null','Invalid record version')
 d.need(type(ref['bytes']) is int and 0<ref['bytes']<=4_000_000,'Record size outside bound')
 d.need(ref['sha256_base64']==base64.b64encode(bytes.fromhex(ref['sha256'])).decode(),'Reference checksum differs')
 return ref

def reference(raw,version):
 value={'schema':SCHEMA,'bucket':d.BUCKET,'owner':d.ACCOUNT,'key':'reviews/'+sha(raw)+'.json','version_id':version,'bytes':len(raw),'sha256':sha(raw),'sha256_base64':base64.b64encode(hashlib.sha256(raw).digest()).decode(),'encryption':'AES256'}
 return validate_reference(value)
def decode(ref,raw,metadata):
 validate_reference(ref)
 d.need(len(raw)==ref['bytes'] and sha(raw)==ref['sha256'],'Private record bytes differ')
 d.need(metadata.get('VersionId')==ref['version_id'] and metadata.get('ContentLength')==ref['bytes'] and metadata.get('ChecksumSHA256')==ref['sha256_base64'] and metadata.get('ServerSideEncryption')=='AES256' and metadata.get('ChecksumType','FULL_OBJECT')=='FULL_OBJECT','Private record metadata differs')
 return json.loads(raw)
def fetch(ref):
 validate_reference(ref)
 with tempfile.TemporaryDirectory() as td:
  output=Path(td)/'record.json'
  args=['aws','s3api','get-object','--bucket',d.BUCKET,'--key',ref['key'],'--version-id',ref['version_id'],'--expected-bucket-owner',d.ACCOUNT,'--checksum-mode','ENABLED','--region',d.REGION,'--output','json','--no-cli-pager',str(output)]
  result=subprocess.run(args,check=True,capture_output=True,env={**os.environ,'AWS_MAX_ATTEMPTS':'1'})
  return decode(ref,output.read_bytes(),json.loads(result.stdout))
def reject_credentials_and_transcripts(value):
 # A defense in depth check, not a claim that heuristic scanning discovers all secrets.
 forbidden={'password','awssecretaccesskey','secretaccesskey','accesskeyid','authorization','accesstoken','refreshtoken','sessiontoken','credentials','transcript','approvaltranscript','userapprovaltranscript','rawcloudtrailevent','apikey','secretkey','privatekey','awsaccesskeyid'}
 if isinstance(value,dict):
  for k,v in value.items():
   normalized=re.sub('[^a-z0-9]','',k.lower())
   d.need(normalized not in forbidden and not (normalized.endswith(('password','secret','token')) and normalized not in ['clienttoken','clientrequesttoken','servicetoken']),'Private record contains prohibited credential/transcript fields')
   reject_credentials_and_transcripts(v)
 elif isinstance(value,list):
  for v in value:reject_credentials_and_transcripts(v)
 elif isinstance(value,str):
  d.need(not any(x in value for x in ['-----BEGIN PRIVATE KEY-----','-----BEGIN RSA PRIVATE KEY-----','x-amz-security-token=','AWS_SECRET_ACCESS_KEY=','Authorization: Bearer ']),'Private record contains prohibited credential material')
  if value.lstrip().startswith(('{','[')):
   try:parsed=json.loads(value)
   except (ValueError,TypeError):parsed=None
   if isinstance(parsed,(dict,list)):reject_credentials_and_transcripts(parsed)
def safe_bucket(c):
 # These metadata reads are already in the installed publisher foundation policy.
 owner={'bucket':d.BUCKET,'expected_bucket_owner':d.ACCOUNT}
 block=d.aws('s3api','get-public-access-block',**owner)['PublicAccessBlockConfiguration']
 d.need(all(block.get(k) is True for k in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']),'Private bucket public-access protection changed')
 policy=json.loads(d.aws('s3api','get-bucket-policy',**owner)['Policy']);d.need(d.sha(d.canonical(policy))==c['asset_policy_sha256'],'Private bucket policy differs from approved exact policy')
 d.need(d.aws('s3api','get-bucket-versioning',**owner).get('Status')=='Enabled','Private record versioning is not enabled')
 rules=d.aws('s3api','get-bucket-ownership-controls',**owner)['OwnershipControls']['Rules'];d.need(rules==[{'ObjectOwnership':'BucketOwnerEnforced'}],'Private bucket ownership changed')
 try:life=d.aws('s3api','get-bucket-lifecycle-configuration',**owner);d.need(not life.get('Rules'),'Private record expiry changed')
 except subprocess.CalledProcessError as error:d.need('(NoSuchLifecycleConfiguration)' in (error.stderr or ''),'Cannot establish lifecycle absence')

def validate_event_projections(value):
 if isinstance(value,dict):
  if 'eventSource' in value and 'eventName' in value:
   d.need(value==bounded_operator_event(value),'Unprojected operator event is not permitted in a persisted record')
  for child in value.values():validate_event_projections(child)
 elif isinstance(value,list):
  for child in value:validate_event_projections(child)

def seal(path,out):
 java_mode=d.load(d.CONFIG).get('mode')=='two-app-java'
 c=d.java_active() if java_mode else d.active();d.need(c.get('private_checkpoint_upload_approved') is True and c.get('public_checkpoint_reference_sharing_approved') is True,'Checkpoint storage/public-reference sharing not approved')
 record=d.load(path);d.need(record.get('kind') in KINDS and record['kind'].startswith('java-')==java_mode,'Record kind cannot cross activation scopes')
 d.source_gate();actor=d.aws('sts','get-caller-identity')
 d.need(actor.get('Account')==d.ACCOUNT and actor.get('Arn')==f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkFilePublisher/bible-publish-{os.environ["GITHUB_RUN_ID"]}','Wrong private-record publisher')
 safe_bucket(c)
 reject_credentials_and_transcripts(record);validate_event_projections(record)
 raw=d.canonical(record);key='reviews/'+sha(raw)+'.json';expected={'key':key,'bytes':len(raw),'sha256_base64':base64.b64encode(hashlib.sha256(raw).digest()).decode()}
 d.need(0<len(raw)<=4_000_000,'Private record exceeds reviewed bound')
 # Never upload again on local replay or a 409/412/uncertain result. Keep the same content key.
 d.exclusive_intent(Path(out).with_suffix('.upload-intent.json'),{'key':key,'sha256':sha(raw),'bytes':len(raw)})
 with tempfile.TemporaryDirectory() as td:
  frozen=Path(td)/'record.json';frozen.write_bytes(raw)
  result=d.aws('s3api','put-object',bucket=d.BUCKET,key=key,body=str(frozen),expected_bucket_owner=d.ACCOUNT,server_side_encryption='AES256',storage_class='STANDARD',checksum_algorithm='SHA256',checksum_sha256=expected['sha256_base64'],if_none_match='*')
 d.need(result.get('VersionId') not in [None,'','null'],'Uncertain private-record upload; reconcile, never repeat automatically')
 h=d.aws('s3api','head-object',bucket=d.BUCKET,key=key,version_id=result['VersionId'],expected_bucket_owner=d.ACCOUNT,checksum_mode='ENABLED');d.object_receipt(h,expected)
 ref=reference(raw,result['VersionId']);fetched=fetch(ref);d.need(d.canonical(fetched)==raw,'Private record readback differs')
 Path(out).parent.mkdir(parents=True,exist_ok=True);Path(out).write_bytes(d.canonical(ref));Path(out).chmod(0o600)

def bounded_operator_event(event):
 # Keep only fields used by creation_evidence. Do not persist full CloudTrail identity/credentials.
 fields=['eventID','requestID','eventTime','eventName','eventSource','awsRegion','recipientAccountId']
 result={k:event[k] for k in fields}
 d.need(not event.get('errorCode') and not event.get('errorMessage'),'Failed operator event')
 identity=event['userIdentity'];result['userIdentity']={'arn':identity['arn'],'sessionContext':{'sessionIssuer':{'arn':identity['sessionContext']['sessionIssuer']['arn']}}}
 params=event['requestParameters'];required=['stackName','changeSetName','changeSetType','capabilities','clientToken','description']
 result['requestParameters']={k:params[k] for k in required};result['requestParameters']['roleARN']=params.get('roleARN',params.get('roleArn'));result['requestParameters']['templateURL']=params.get('templateURL',params.get('templateUrl'))
 result['responseElements']={k:event['responseElements'][k] for k in ['id','stackId']};reject_credentials_and_transcripts(result);return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();seal(args.input,args.output)
if __name__=='__main__':main()
