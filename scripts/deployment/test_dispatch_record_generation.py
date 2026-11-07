import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import MagicMock,patch
import java_adapter as a

class GeneratedBinding(unittest.TestCase):
 def test_actual_main_generators_stamp_all_four_private_record_kinds(self):
  for mode,kind,filename in [('review','java-review','review.json'),('stage','java-intent','operation.json'),('submit','java-response','response.json'),('operator-reconcile','java-operator-evidence','operator-evidence.json')]:
   with self.subTest(mode=mode),tempfile.TemporaryDirectory() as td:
    adapter=MagicMock();adapter.source='a'*40;adapter.run_id=17;adapter.attempt=1;adapter.pins={'plan_sha256':'b'*64};adapter.dispatch_approval_sha256='c'*64;adapter.t.now.return_value=datetime(2026,11,7,tzinfo=timezone.utc)
    request={'ChangeSetName':'exact'};operation={'state':'phase1_create_intent','history':[{'request':request}],'plan':{},'plan_digest':'b'*64}
    review={'plan':{},'controls':{},'template_receipts':{}}
    checkpoint={'operation':operation,'controls':{},'template_receipts':{},'deployment_id':3,'request_sha256':'d'*64}
    adapter.read_artifact.side_effect=lambda aid,*args: review if aid=='10' else checkpoint
    adapter.next_intent.return_value=operation;adapter.submit.return_value={'ok':True};adapter.describe.return_value={}
    adapter.t.private_record.return_value={'review':{'controls_sha256':a.digest({})},'baseline':{},'candidate':{},'assets':{},'controls':{},'template_receipts':{},'event':{},'event_sha256':a.digest({})}
    inputs={'mode':'release' if mode in ['stage','submit'] else mode,'bundle_ref':'{}','review_artifact_id':'10','review_artifact_digest':'sha256:'+'e'*64,'resume_artifact_id':'11' if mode=='operator-reconcile' else '', 'resume_artifact_digest':'sha256:'+'f'*64,'operator_private_ref':{}}
    with patch.object(a,'dispatch',return_value=({},inputs,adapter)),patch('sys.argv',['java_adapter.py',mode,'--out',td]),patch.object(a,'deployment_for_run',return_value=3),patch.object(a.private,'validate_reference',side_effect=lambda x:x),patch.object(a.private,'bounded_operator_event',side_effect=lambda x:x),patch.object(a.j,'derive',return_value={}),patch.object(a,'pinset',return_value=adapter.pins),patch('source_promotion.review_bundle'),patch.object(Path,'read_text',return_value='{}'),patch.dict('os.environ',{'INTENT_ARTIFACT_ID':'11','INTENT_ARTIFACT_DIGEST':'sha256:'+'f'*64},clear=True):
     a.main()
    record=json.loads((Path(td)/filename).read_text())
    self.assertEqual(record['kind'],kind);self.assertEqual(record['dispatch_approval_sha256'],'c'*64)

if __name__=='__main__':unittest.main()
