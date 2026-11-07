import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch,Mock
import zipfile
import prove_reproducible as p

class Proof(unittest.TestCase):
 def fixture(self,root,content=b'same'):
  (root/'target').mkdir(parents=True,exist_ok=True);(root/'.infrastructure').mkdir(exist_ok=True)
  out=root/'.private/cdk-source';out.mkdir(parents=True,exist_ok=True)
  with zipfile.ZipFile(root/'target/bibleverse-2.0.0-aws.jar','w') as z:z.writestr(zipfile.ZipInfo('META-INF/build-info.properties',(2026,11,7,0,0,0)),content)
  (out/'bible-verse-stack.template.json').write_text(json.dumps({'Resources':{'Fixture':{'PRIVATE':'not emitted'}}}))
  (out/'asset.jar').write_bytes(content)
  (out/'bible-verse-stack.assets.json').write_text(json.dumps({'files':{'hash':{'source':{'path':'asset.jar'}}}}))
 def test_two_distinct_clean_worktrees_shared_commit_and_separated_times(self):
  calls=[];sleep=Mock()
  def runner(command,cwd,log):
   calls.append((command,cwd))
   if command[:3]==['git','worktree','add']:self.fixture(Path(command[4]))
  with patch.object(p.subprocess,'run',return_value=SimpleNamespace(stdout='a'*40)),patch.dict('os.environ',{},clear=True):result=p.prove(Path('/repo'),'a'*40,runner,sleep)
  self.assertTrue(result['passed']);sleep.assert_called_once_with(3)
  builds=[x for x in calls if x[0][0]=='python'];self.assertEqual(len(builds),2);self.assertNotEqual(builds[0][1],builds[1][1])
  for command,_ in builds:self.assertEqual(command[-1],'a'*40)
  synth=[x[0] for x in calls if 'synth' in x[0]];self.assertEqual(len(synth),2);self.assertTrue(all('--no-lookups' in x for x in synth))
  self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('not emitted',json.dumps(result))
 def test_full_jar_mismatch_has_safe_hash_only_diagnostics(self):
  with tempfile.TemporaryDirectory() as td:
   a=Path(td)/'a';b=Path(td)/'b';self.fixture(a);self.fixture(b,b'different')
   result=p.compare(p.snapshot(a),p.snapshot(b));self.assertFalse(result['passed']);self.assertIn('jar_sha256',result['differing_hash_fields']);self.assertNotIn('different',json.dumps(result));self.assertNotIn('build-info.properties',json.dumps(result))
 def test_entry_diagnostics_are_bounded_hashed_and_complete(self):
  before=[{'path_sha256':p.digest(str(i).encode()),'bytes_sha256':'a'*64,'timestamp':[2026,1,1,0,0,0]} for i in range(60)]
  after=[{**x,'bytes_sha256':'b'*64} for x in before]
  result=p.entry_differences(before,after)
  self.assertEqual(result['changed_entry_count'],60);self.assertEqual(len(result['entry_differences']),50)
  self.assertTrue(result['entry_differences_truncated']);self.assertFalse(result['entry_differences_complete'])
  for item in result['entry_differences']:
   self.assertEqual(set(item),{'path_sha256','occurrence','before','after'})
   for side in ['before','after']:
    self.assertEqual(set(item[side]),{'content_sha256','metadata_sha256'})
    self.assertTrue(all(len(v)==64 for v in item[side].values()))
  small=p.entry_differences(before[:1],after[:1]);self.assertTrue(small['entry_differences_complete']);self.assertFalse(small['entry_differences_truncated'])
  self.assertEqual(p.entry_differences(before[:1],[])['entry_differences'][0]['after'],None)
  duplicate=p.entry_differences(before[:1]*2,before[:1]);self.assertEqual(duplicate['changed_entry_count'],1)

 def test_wrong_checkout_or_credentials_blocks_before_worktree(self):
  for head,env in [('b'*40,{}),('a'*40,{'AWS_ACCESS_KEY_ID':'fixture'})]:
   runner=Mock()
   with patch.object(p.subprocess,'run',return_value=SimpleNamespace(stdout=head)),patch.dict('os.environ',env,clear=True):
    with self.assertRaises(ValueError):p.prove(Path('/repo'),'a'*40,runner)
   runner.assert_not_called()
 def test_manifest_paths_cannot_escape_and_no_public_artifact_workflow(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);self.fixture(root);(root/'.private/cdk-source/bible-verse-stack.assets.json').write_text('{"files":{"x":{"source":{"path":"../../secret"}}}}')
   with self.assertRaises(ValueError):p.snapshot(root)
  text=(Path(__file__).resolve().parents[2]/'.github/workflows/reproducible-build-proof.yml').read_text()
  self.assertIn('timeout-minutes: 20',text);self.assertIn('workflow_dispatch:',text)
  for forbidden in ['id-token:','environment: production','upload-artifact','configure-aws','cdk deploy','push:']:self.assertNotIn(forbidden,text)

if __name__=='__main__':unittest.main()
