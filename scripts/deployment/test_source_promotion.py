"""Source-derived offline fixtures only, no live template or approval generation."""
import base64,copy,json,unittest,tempfile,hashlib
from pathlib import Path
import source_promotion as p
import java_release as j
ROOT=Path(__file__).parent
def fixture():
 source=json.loads((ROOT/'fixtures/java-release/baseline.json').read_text())
 live=copy.deepcopy(source)
 a={'bucket':j.BUCKET,'owner':'664759038511','encryption':'AES256','key':'assets/'+'1'*64+'.jar','version_id':'actual-fixture-version','bytes':123,'sha256_base64':base64.b64encode(b'1'*32).decode(),'checksum_type':'FULL_OBJECT'}
 assets={fid:copy.deepcopy(a) for fid in j.FUNCTIONS}
 return source,live,assets
def review(s,n,l,a,out,mode,oldhash):
 return {'enabled':False,'mode':mode,'source_baseline_sha256':p.sha(s),'source_candidate_sha256':p.sha(n),'live_baseline_sha256':p.sha(l),'assets_sha256':p.sha(a),'promoted_candidate_sha256':p.sha(out),'old_code_sha256':oldhash,'source_code_refs':{fid:n['Resources'][fid]['Properties']['Code'] for fid in j.FUNCTIONS}}
def release_source(s,suffix):
 n=copy.deepcopy(s)
 for fid,info in j.FUNCTIONS.items():
  old=j.version_ref(n['Resources'][info['alias']]);new=old+'Fixture'+suffix
  n['Resources'][new]=n['Resources'].pop(old)
  n['Resources'][info['alias']]['Properties']['FunctionVersion']={'Fn::GetAtt':[new,'Version']}
  n['Resources'][fid]['Properties']['Code']['S3Key']=suffix*64+'.jar'
 return n
def expected(l,n,a,release=False):
 out=copy.deepcopy(l)
 for fid,info in j.FUNCTIONS.items():
  out['Resources'][fid]['Properties']['Code']={'S3Bucket':j.BUCKET,'S3Key':a[fid]['key'],'S3ObjectVersion':a[fid]['version_id']}
  if release:
   old=j.version_ref(l['Resources'][info['alias']]);new=j.version_ref(n['Resources'][info['alias']]);v=out['Resources'].pop(old);v['Properties']['CodeSha256']=a[fid]['sha256_base64'];out['Resources'][new]=v;out['Resources'][info['alias']]['Properties']['FunctionVersion']={'Fn::GetAtt':[new,'Version']}
 return out
class Promotion(unittest.TestCase):
 def test_cli_asset_metadata_tracks_only_source_code_hash(self):
  s,l,a=fixture()
  for fid in j.FUNCTIONS:
   md={'aws:asset:path':'asset.'+s['Resources'][fid]['Properties']['Code']['S3Key'],'aws:asset:is-bundled':False,'aws:asset:property':'Code'}
   s['Resources'][fid]['Metadata']=copy.deepcopy(md);l['Resources'][fid]['Metadata']=copy.deepcopy(md)
  for suffix in ['2','3']:
   n=release_source(s,suffix)
   for fid in j.FUNCTIONS:n['Resources'][fid]['Metadata']['aws:asset:path']='asset.'+n['Resources'][fid]['Properties']['Code']['S3Key']
   a2=copy.deepcopy(a)
   for fid in a2:a2[fid]['sha256_base64']=base64.b64encode(suffix.encode()*32).decode()
   out=expected(l,n,a2,True);r=review(s,n,l,a2,out,'ordinary-java',{f:a[f]['sha256_base64'] for f in a})
   self.assertEqual(p.promote(s,n,l,a2,r),out)
   s,l,a=n,out,a2
  fid=next(iter(j.FUNCTIONS))
  for key,value in [('aws:asset:path','asset.'+'f'*64+'.jar'),('aws:asset:path','secret/path'),('aws:asset:property','Other'),('aws:asset:is-bundled',True)]:
   bad=copy.deepcopy(s);bad['Resources'][fid]['Metadata'][key]=value
   with self.subTest(key=key,value=value),self.assertRaises(ValueError):p.split(bad)
  bad=copy.deepcopy(l);bad['Resources'][fid]['Metadata']['other']='changed'
  with self.assertRaises(ValueError):p.bridge(s,bad)
 def test_initial_byte_identical_preserves_old_versions_aliases(self):
  s,l,a=fixture();out=expected(l,s,a);r=review(s,s,l,a,out,'identical-cutover',{f:a[f]['sha256_base64'] for f in a})
  self.assertEqual(p.promote(s,s,l,a,r),out)
  self.assertEqual({k:v for k,v in l['Resources'].items() if v['Type'] in ['AWS::Lambda::Version','AWS::Lambda::Alias']},{k:v for k,v in out['Resources'].items() if v['Type'] in ['AWS::Lambda::Version','AWS::Lambda::Alias']})
 def test_two_successive_releases_and_phase_protocol(self):
  s,l,a=fixture();l=expected(l,s,a)
  for step in ['2','3']:
   oldhash={f:a[f]['sha256_base64'] for f in a};n=release_source(s,step)
   for v in a.values():v.update(key='assets/'+step*64+'.jar',version_id='fixture-'+step,sha256_base64=base64.b64encode(step.encode()*32).decode())
   out=expected(l,n,a,True);r=review(s,n,l,a,out,'ordinary-java',oldhash)
   self.assertEqual(p.promote(s,n,l,a,r),out)
   # Existing two-phase protocol accepts the promoted canonical candidate.
   phase1=copy.deepcopy(l);old_versions={}
   for fid,info in j.FUNCTIONS.items():
    old=j.version_ref(l['Resources'][info['alias']]);new=j.version_ref(out['Resources'][info['alias']]);phase1['Resources'][fid]['Properties']['Code']=out['Resources'][fid]['Properties']['Code'];phase1['Resources'][new]=out['Resources'][new]
    old_versions[fid]={'logical_id':old,'version':str(32+int(step)),'physical_id':f'arn:aws:lambda:us-east-1:664759038511:function:{info["name"]}:{32+int(step)}'}
   jr={'kind':'ordinary-java-two-phase','activation':False,'baseline_sha256':p.sha(l),'candidate_sha256':p.sha(out),'assets_sha256':p.sha(a),'source_commit':'a'*40,'capabilities':['CAPABILITY_NAMED_IAM'],'old_versions':old_versions,'phase1_sha256':p.sha(phase1),'phase2_sha256':p.sha(out)}
   result=j.derive(l,out,a,jr);self.assertEqual(result['phase2'],out)
   s,l=n,out
 def test_every_nonapp_resource_and_config_change_rejected(self):
  s,l,a=fixture();n=release_source(s,'2');out=expected(l,n,a,True);oldhash={f:base64.b64encode(b'0'*32).decode() for f in a}
  for rid in n['Resources']:
   bad=copy.deepcopy(n);bad['Resources'][rid]['Metadata']={'unexpected':True}
   r=review(s,bad,l,a,out,'ordinary-java',oldhash)
   with self.subTest(resource=rid),self.assertRaises(ValueError):p.promote(s,bad,l,a,r)
  for field in ['Runtime','MemorySize','Role','Environment','SnapStart','DependsOn']:
   bad=copy.deepcopy(n);bad['Resources'][next(iter(j.FUNCTIONS))]['Properties'][field]='wrong';r=review(s,bad,l,a,out,'ordinary-java',oldhash)
   with self.subTest(field=field),self.assertRaises(ValueError):p.promote(s,bad,l,a,r)
 def test_review_stage_binds_actual_source_and_jar(self):
  s,l,a=fixture();n=release_source(s,'2');raw=b'new reviewed fixture JAR'
  for v in a.values():v.update(bytes=len(raw),sha256_base64=base64.b64encode(hashlib.sha256(raw).digest()).decode())
  out=expected(l,n,a,True);r=review(s,n,l,a,out,'ordinary-java',{fid:base64.b64encode(b'0'*32).decode() for fid in a})
  sp={'source_baseline':s,'source_current':n,'review':r};bundle={'source_promotion':sp,'review':{'source_promotion_sha256':p.sha(sp)},'baseline':l,'candidate':out,'assets':a}
  with tempfile.TemporaryDirectory() as directory:
   jar=Path(directory)/'jar';jar.write_bytes(raw)
   self.assertEqual(p.review_bundle(bundle,n,jar),out)
   bad=copy.deepcopy(n);bad['Description']='drift'
   with self.assertRaises(ValueError):p.review_bundle(bundle,bad,jar)
   jar.write_bytes(b'wrong')
   with self.assertRaises(ValueError):p.review_bundle(bundle,n,jar)
   jar.write_bytes(raw);bad=copy.deepcopy(bundle);bad['source_promotion']['extra']='payload'
   with self.assertRaises(ValueError):p.review_bundle(bad,n,jar)
 def test_private_metadata_preserved_and_other_baseline_drift_rejected(self):
  s,l,a=fixture();l['Resources']['CDKMetadata']={'Type':'AWS::CDK::Metadata','Properties':{'Analytics':'fixture'}}
  out=expected(l,s,a);r=review(s,s,l,a,out,'identical-cutover',{fid:a[fid]['sha256_base64'] for fid in a})
  self.assertEqual(p.promote(s,s,l,a,r)['Resources']['CDKMetadata'],l['Resources']['CDKMetadata'])
  bad=copy.deepcopy(l);bad['Resources']['Other']={'Type':'AWS::S3::Bucket','Properties':{}}
  with self.assertRaises(ValueError):p.bridge(s,bad)
  bad=copy.deepcopy(l);bad['Resources'][next(iter(j.FUNCTIONS))]['Properties']['MemorySize']=2048
  with self.assertRaises(ValueError):p.bridge(s,bad)
 def test_pins_and_unchanged_version_rejected(self):
  s,l,a=fixture();out=expected(l,s,a);r=review(s,s,l,a,out,'identical-cutover',{f:a[f]['sha256_base64'] for f in a})
  for key in ['source_baseline_sha256','source_candidate_sha256','live_baseline_sha256','assets_sha256','promoted_candidate_sha256']:
   bad=copy.deepcopy(r);bad[key]='0'*64
   with self.subTest(key=key),self.assertRaises(ValueError):p.promote(s,s,l,a,bad)
  r['mode']='ordinary-java'
  with self.assertRaises(ValueError):p.promote(s,s,l,a,r)
if __name__=='__main__':unittest.main()
