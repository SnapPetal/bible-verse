"""Privacy tests: all AWS/GitHub behavior is mocked; no upload or download occurs."""
import contextlib,copy,hashlib,io,json,os,re,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import bible_deploy as d
import private_records as p
from test_java_adapter import Cloud,plan
class Privacy(unittest.TestCase):
 def test_public_reference_contains_no_private_record_fields(self):
  record={'kind':'java-review','template':{'Resources':{'Function':{'Properties':{'Environment':{'Variables':{'TENANT_LABEL':'PRIVATE-TENANT-SENTINEL'}}}}}},'event':{'userIdentity':{'arn':'PRIVATE-OPERATOR-SENTINEL'}}}
  raw=d.canonical(record);ref=p.reference(raw,'V'*32);public=d.canonical(ref)
  self.assertEqual(set(ref),p.FIELDS)
  for marker in ['Resources','Environment','PRIVATE-TENANT-SENTINEL','userIdentity','PRIVATE-OPERATOR-SENTINEL']:self.assertNotIn(marker.encode(),public)
 def test_public_extra_fields_never_pass(self):
  ref=p.reference(b'{}','V'*32)
  for key,value in [('template',{}),('Environment',{}),('event',{}),('credentials','credential-sentinel'),('transcript','approval-sentinel')]:
   with self.subTest(key=key),self.assertRaises(ValueError):p.validate_reference({**ref,key:value})
 def test_credentials_and_approval_transcripts_rejected_without_logging_values(self):
  marker='HIGHLY-PRIVATE-SENTINEL'
  for value in [{'credentials':marker},{'user_approval_transcript':marker},{'event':{'userIdentity':{'accessKeyId':marker}}},{'Environment':{'Variables':{'API_TOKEN':marker}}},{'Environment':{'Variables':{'CLIENT_SECRET':marker}}},{'nested':'-----BEGIN PRIVATE KEY-----'+marker},{'BeforeContext':json.dumps({'Environment':{'Variables':{'PASSWORD':marker}}})}]:
   stdout=io.StringIO()
   with contextlib.redirect_stdout(stdout),self.assertRaises(ValueError) as error:p.reject_credentials_and_transcripts(value)
   self.assertNotIn(marker,str(error.exception));self.assertEqual(stdout.getvalue(),'')
 def test_operator_projection_drops_raw_identity_material(self):
  e={'eventID':'e','requestID':'r','eventTime':'2026-11-07T15:00:00Z','eventName':'CreateChangeSet','eventSource':'cloudformation.amazonaws.com','awsRegion':'us-east-1','recipientAccountId':d.ACCOUNT,'userIdentity':{'arn':'actor','accessKeyId':'NEVER-PERSIST','principalId':'unused','sessionContext':{'sessionIssuer':{'arn':'role','userName':'unused'}}},'requestParameters':{'stackName':'stack','changeSetName':'name','changeSetType':'UPDATE','capabilities':[],'clientToken':'idempotency','description':'bounded','roleARN':'role','templateURL':'https://example.invalid/template'},'responseElements':{'id':'cs','stackId':'stack','unused':'extra'},'rawExtra':'DROP-ME'}
  reduced=p.bounded_operator_event(e);raw=d.canonical(reduced)
  self.assertNotIn(b'NEVER-PERSIST',raw);self.assertNotIn(b'DROP-ME',raw);self.assertNotIn('principalId',reduced['userIdentity'])
 def test_private_body_and_metadata_integrity_required(self):
  raw=b'{"kind":"java-response"}';ref=p.reference(raw,'V'*32);meta={'VersionId':'V'*32,'ContentLength':len(raw),'ChecksumSHA256':ref['sha256_base64'],'ServerSideEncryption':'AES256'}
  self.assertEqual(p.decode(ref,raw,meta)['kind'],'java-response')
  with self.assertRaises(ValueError):p.decode(ref,raw+b' ',meta)
  for key,value in [('VersionId','other'),('ServerSideEncryption','aws:kms'),('ChecksumSHA256','wrong'),('ContentLength',1)]:
   with self.subTest(key=key),self.assertRaises(ValueError):p.decode(ref,raw,{**meta,key:value})
 def test_unapproved_private_and_public_sharing_stop_before_aws(self):
  for private,public in [(False,False),(True,False),(False,True)]:
   with patch.object(d,'active',return_value={'private_checkpoint_upload_approved':private,'public_checkpoint_reference_sharing_approved':public}),patch.object(d,'aws') as aws:
    with self.assertRaises(ValueError):p.seal(Path('missing'),Path('missing-output'))
    aws.assert_not_called()
 def test_java_activation_cannot_seal_migration_records(self):
  c={'mode':'two-app-java','private_checkpoint_upload_approved':True,'public_checkpoint_reference_sharing_approved':True}
  def load(path):return c if path==d.CONFIG else {'kind':'migration-intent'}
  with patch.object(d,'load',side_effect=load),patch.object(d,'java_active',return_value=c),patch.object(d,'aws') as aws:
   with self.assertRaises(ValueError):p.seal(Path('record'),Path('out'))
   aws.assert_not_called()
 def test_bucket_privacy_failure_stops_before_upload(self):
  for key in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']:
   blocks={x:True for x in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']};blocks[key]=False
   with patch.object(d,'aws',return_value={'PublicAccessBlockConfiguration':blocks}) as aws:
    with self.assertRaises(ValueError):p.safe_bucket({'asset_policy_sha256':'unused'})
    self.assertEqual(aws.call_count,1)
 def test_seal_conditional_private_write_emits_only_flat_public_reference(self):
  record={'kind':'migration-response','response':{'Id':'private-change-set-sentinel'}};raw=d.canonical(record);expected=p.reference(raw,'V'*32);seen=[]
  def aws(service,operation,**kw):
   seen.append((operation,kw))
   if operation=='get-caller-identity':return {'Account':d.ACCOUNT,'Arn':f'arn:aws:sts::{d.ACCOUNT}:assumed-role/BibleVerseCdkFilePublisher/bible-publish-1'}
   if operation=='put-object':return {'VersionId':'V'*32}
   if operation=='head-object':return {'VersionId':'V'*32,'ContentLength':len(raw),'ChecksumSHA256':expected['sha256_base64'],'ServerSideEncryption':'AES256'}
   raise AssertionError(operation)
  with tempfile.TemporaryDirectory() as td,patch.object(d,'active',return_value={'private_checkpoint_upload_approved':True,'public_checkpoint_reference_sharing_approved':True}),patch.object(d,'source_gate'),patch.object(p,'safe_bucket'),patch.object(d,'aws',side_effect=aws),patch.object(p,'fetch',return_value=record),patch.dict(os.environ,{'GITHUB_RUN_ID':'1'}):
   source=Path(td)/'private.json';source.write_bytes(raw);out=Path(td)/'public'/'record-ref.json';stdout=io.StringIO()
   with contextlib.redirect_stdout(stdout):p.seal(source,out)
   self.assertEqual(json.loads(out.read_text()),expected);self.assertNotIn('private-change-set-sentinel',out.read_text());self.assertEqual(stdout.getvalue(),'')
   put=next(kw for op,kw in seen if op=='put-object');self.assertEqual(put['if_none_match'],'*');self.assertEqual(put['storage_class'],'STANDARD');self.assertEqual(put['key'],expected['key'])
 def test_workflow_artifacts_are_references_not_records(self):
  root=Path(__file__).resolve().parents[2]
  for name in ['java-release.yml']:
   text=(root/'.github/workflows'/name).read_text();paths=re.findall(r'^          path: (.+)$',text,re.M);self.assertTrue(paths)
   self.assertTrue(all(x.endswith('/record-ref.json') for x in paths));self.assertIn('./.github/actions/private-checkpoint',text)
  retired=(root/'.github/workflows/deploy.yml').read_text()
  for forbidden in ['upload-artifact','configure-aws-credentials','private-checkpoint','  push:','cdk deploy']:self.assertNotIn(forbidden,retired)
  self.assertIn('workflow_dispatch:',retired);self.assertIn('python -m unittest',retired)
  self.assertIn('.infrastructure/deployment/publication-plan.json',(root/'.gitignore').read_text())
 def test_mock_public_archives_store_only_private_refs(self):
  cloud=Cloud(plan());record={'kind':'java-review','plan_sha256':'a'*64,'run_id':101,'private_template':{'Environment':'PRIVATE-SENTINEL'}};ident,_=cloud.upload_model(record)
  with zipfile.ZipFile(io.BytesIO(cloud.archive(ident))) as z:
   self.assertEqual(z.namelist(),['record-ref.json']);payload=z.read('record-ref.json');self.assertNotIn(b'PRIVATE-SENTINEL',payload);p.validate_reference(json.loads(payload))
 def test_reference_rejects_urls_free_text_oversize_and_covert_keys(self):
  ref=p.reference(b'{}','V'*32)
  cases=[('bucket','https://bucket.example/?X-Amz-Security-Token=PRIVATE'),('key','reviews/RAW-PRIVATE-CONTENT.json'),('key','reviews/'+ref['sha256']+'.json?secret=PRIVATE'),('version_id','Bearer PRIVATE'),('version_id','x'*2048),('version_id','https://secret.example/credential'),('bytes',True),('metadata',{'event':'PRIVATE'}),('description','PRIVATE'*10000)]
  for key,value in cases:
   with self.subTest(key=key,value_length=len(str(value))),self.assertRaises(ValueError):p.validate_reference({**ref,key:value})
class VersionFormat(unittest.TestCase):
 def test_bounded_opaque_versions(self):
  for value in ['a','A'*1024,'opaque+/=._~-value']:
   self.assertEqual(p.reference(b'{}',value)['version_id'],value)
  for value in ['', 'null','a'*1025,'unsafe?query','unsafe\nvalue','https://secret.example','Bearer secret']:
   with self.subTest(value=value[:20]),self.assertRaises(ValueError):p.reference(b'{}',value)
if __name__=='__main__':unittest.main()
