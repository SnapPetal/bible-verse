import copy
import json
import unittest
from unittest.mock import patch
import dispatch_approval as a
import private_records as p

class Approval(unittest.TestCase):
 def setUp(self):
  self.pins={k:('a'*40 if k=='source_commit' else 'b'*64) for k in a.PIN_KEYS}
  self.inputs={'mode':'review','review_pins':json.dumps(self.pins),'bundle_ref':json.dumps(p.reference(b'fixture','version')),'approve_private_records':'true','approve_public_references':True}
  self.event={'sender':{'id':6395602},'inputs':self.inputs}
  self.env={'GITHUB_REPOSITORY':a.REPO,'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_WORKFLOW_REF':a.REPO+'/'+a.WORKFLOW+'@refs/heads/main','GITHUB_SHA':'a'*40,'GITHUB_ACTOR_ID':'6395602','GITHUB_RUN_ID':'17','GITHUB_RUN_ATTEMPT':'1'}
  self.binding={'disclosure':a.DISCLOSURE,'pins':self.pins,'mode':'review','bundle_ref':json.loads(self.inputs['bundle_ref']),'review_artifact_id':'','review_artifact_digest':''}
  self.config={'java_release_actor_ids':[6395602],'reviewed_dispatch_disclosures':[self.binding]}
  self.run={'id':17,'repository':{'full_name':a.REPO},'path':a.WORKFLOW,'head_sha':'a'*40,'head_branch':'main','event':'workflow_dispatch','run_attempt':1,'actor':{'id':6395602},'triggering_actor':{'id':6395602}}
 def read(self,path):return self.run if '/actions/' in path else {'object':{'sha':'a'*40}}
 def go(self,record=None):return a.validate(self.config,self.event,self.env,self.read,record)
 def test_exact_true_variants(self):
  self.assertEqual({k:v for k,v in self.go().items() if k!='request_sha256'},self.binding)
  self.inputs.update(approve_private_records=True,approve_public_references='true');self.go()
 def test_false_missing_forged_booleans(self):
  for key in ['approve_private_records','approve_public_references']:
   for value in [False,'false','TRUE',1,'1',None,{},[]]:
    with self.subTest(key=key,value=value):
     old=self.inputs[key];self.inputs[key]=value
     with self.assertRaises(ValueError):self.go()
     self.inputs[key]=old
   old=self.inputs.pop(key)
   with self.assertRaises(ValueError):self.go()
   self.inputs[key]=old
 def test_event_binds_package_without_committed_registry(self):
  self.config.pop('reviewed_dispatch_disclosures')
  old=self.go()['request_sha256']
  self.inputs['bundle_ref']=json.dumps(p.reference(b'other','version'))
  self.assertNotEqual(old,self.go()['request_sha256'])
  # Consent alone does not prove package identity: the unchanged Adapter must
  # compare the selected package with all exact plan/template/assets pins.
 def test_invalid_pins_or_unknown_keys(self):
  for key in a.PIN_KEYS|{'extra'}:
   pins=copy.deepcopy(self.pins);pins[key]='invalid';self.inputs['review_pins']=json.dumps(pins)
   with self.subTest(key=key),self.assertRaises(ValueError):self.go()
 def test_wrong_actor_or_run_provenance(self):
  for key,value in [('actor',{'id':1}),('triggering_actor',{'id':1}),('id',18),('run_attempt',2),('head_sha','c'*40),('head_branch','other'),('event','push'),('path','other'),('repository',{'full_name':'other'})]:
   old=self.run[key];self.run[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):self.go()
   self.run[key]=old
  self.event['sender']['id']=1
  with self.assertRaises(ValueError):self.go()
 def test_wrong_main_and_record_binding(self):
  with patch.object(self,'read',return_value={}):
   with self.assertRaises(ValueError):self.go()
  record={'dispatch_approval_sha256':self.go()['request_sha256'],'kind':'java-review','source_commit':'a'*40,'plan_sha256':'b'*64,'run_id':17};self.go(record)
  for key,value in [('source_commit','c'*40),('plan_sha256','c'*64),('run_id',18)]:
   bad={**record,key:value}
   with self.assertRaises(ValueError):self.go(bad)
 def test_release_requires_exact_review_artifact(self):
  self.inputs.update(mode='release',bundle_ref='',review_artifact_id='12',review_artifact_digest='sha256:'+'d'*64)
  self.binding.update(mode='release',bundle_ref=None,review_artifact_id='12',review_artifact_digest='sha256:'+'d'*64);self.go()
  old=self.go()['request_sha256']
  self.inputs['review_artifact_digest']='sha256:'+'e'*64
  self.assertNotEqual(old,self.go()['request_sha256'])
 def test_record_kind_must_match_dispatch_mode(self):
  for mode, allowed in [('review',{'java-review'}),('release',{'java-intent','java-response'}),('operator-reconcile',{'java-operator-evidence'})]:
   self.setUp()
   if mode!='review':
    self.inputs.update(mode=mode,bundle_ref='',review_artifact_id='12',review_artifact_digest='sha256:'+'d'*64)
    self.binding.update(mode=mode,bundle_ref=None,review_artifact_id='12',review_artifact_digest='sha256:'+'d'*64)
    self.config['java_operator_actor_ids']=[6395602]
   for kind in ['java-review','java-intent','java-response','java-operator-evidence','migration-review',None]:
    record={'dispatch_approval_sha256':self.go()['request_sha256'],'kind':kind,'source_commit':'a'*40,'plan_sha256':'b'*64,'run_id':17}
    if kind in allowed:self.go(record)
    else:
     with self.subTest(mode=mode,kind=kind),self.assertRaisesRegex(ValueError,'kind outside'):self.go(record)

 def test_review_then_release_without_source_commit_change(self):
  self.config.pop('reviewed_dispatch_disclosures')
  review=self.go();source=self.env['GITHUB_SHA']
  self.inputs.update(mode='release',bundle_ref='',review_artifact_id='12',review_artifact_digest='sha256:'+'d'*64)
  self.env['GITHUB_RUN_ID']='18';self.run['id']=18
  release=self.go()
  self.assertEqual(source,self.env['GITHUB_SHA'])
  self.assertEqual(review['pins'],release['pins'])
  self.assertNotEqual(review['request_sha256'],release['request_sha256'])
  self.assertEqual(release['review_artifact_id'],'12')
  # Actual prior review artifact authentication remains in Adapter.read_artifact.

 def test_seal_rejects_changed_reference_or_recovery_in_same_run(self):
  record={'dispatch_approval_sha256':self.go()['request_sha256'],'kind':'java-review','source_commit':'a'*40,'plan_sha256':'b'*64,'run_id':17}
  for key,value in [('bundle_ref',json.dumps(p.reference(b'changed','v2'))),('recovery','{"response_artifact_id":"22"}')]:
   old=self.inputs.get(key);self.inputs[key]=value
   with self.subTest(key=key),self.assertRaisesRegex(ValueError,'approval changed'):self.go(record)
   if old is None:self.inputs.pop(key)
   else:self.inputs[key]=old

 def test_both_production_entrypoints_reuse_validator(self):
  from pathlib import Path
  root=Path(__file__).parent
  self.assertIn('dispatch_approval.authorize(c)',(root/'java_adapter.py').read_text())
  self.assertIn('dispatch_approval.authorize(c,record)',(root/'private_records.py').read_text())

if __name__=='__main__':unittest.main()
