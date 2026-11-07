"""Enhanced-view behavior is mocked, not a live CloudFormation service test."""
import copy,unittest
import bible_deploy as d
import test_migration_adapter as fixture
class Enhanced(unittest.TestCase):
 setUp=fixture.Migration.setUp
 make=fixture.Migration.make
 persist=fixture.Migration.persist
 create=fixture.Migration.create
 def test_basic_conservative_view_is_not_used(self):
  intent,response=self.create();raw=self.cloud.aws;seen=[]
  def aws(service,operation,**kw):
   value=raw(service,operation,**kw)
   if operation=='describe-change-set':
    seen.append(kw.get('include_property_values'))
    if kw.get('include_property_values') is not True:
     value['Changes'].append({'ResourceChange':{'LogicalResourceId':'UnchangedDependency','Action':'Modify','ResourceType':'Custom::CDKBucketDeployment','Replacement':'Conditional'}})
   return value
  self.cloud.aws=aws
  cs=self.adapter.bind_creation(intent,response)
  d.check_changes(cs,{'candidate_sha256':self.p['candidate_sha256'],'baseline':self.p['baseline']},self.p['template'])
  self.assertTrue(seen and all(seen))
  basic=aws('cloudformation','describe-change-set',include_property_values=False)
  basic['ExecutorEvidence']=cs['ExecutorEvidence']
  with self.assertRaises(ValueError):d.check_changes(basic,{'candidate_sha256':self.p['candidate_sha256'],'baseline':self.p['baseline']},self.p['template'])
 def test_unresolved_missing_replacement_and_extra_scope_rejected(self):
  intent,response=self.create();cs=self.adapter.bind_creation(intent,response)
  for fault in ['Conditional','missing','unknown','duplicate','pagination']:
   value=copy.deepcopy(cs)
   if fault=='pagination':value['NextToken']='remaining'
   elif fault=='duplicate':value['Changes'].append(copy.deepcopy(value['Changes'][0]))
   elif fault=='missing':value['Changes'][0]['ResourceChange'].pop('Replacement')
   else:value['Changes'][0]['ResourceChange']['Replacement']=fault
   with self.subTest(fault=fault),self.assertRaises(ValueError):d.check_changes(value,{'candidate_sha256':self.p['candidate_sha256'],'baseline':self.p['baseline']},self.p['template'])
 def test_adapter_rejects_incomplete_page(self):
  self.create();self.cloud.cs['NextToken']='remaining'
  with self.assertRaises(ValueError):self.adapter.describe('exact')
if __name__=='__main__':unittest.main()
