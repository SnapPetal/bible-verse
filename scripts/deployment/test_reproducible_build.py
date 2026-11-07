import unittest
import io
import contextlib
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import reproducible_build as b

class Timestamp(unittest.TestCase):
 def test_same_commit_uses_identical_timestamp_and_command(self):
  first=b.plan('a'*40,'a'*40,'1794052800')
  second=b.plan('a'*40,'a'*40,'1794052800')
  self.assertEqual(first,second)
  self.assertEqual(first['command'][-1],'-Dproject.build.outputTimestamp='+first['output_timestamp'])
 def test_wrong_source_dirty_or_untrusted_epoch_rejected(self):
  for args in [('a'*40,'b'*40,'1794052800'),('a'*40,'a'*40,'1794052800',True),('main','main','1794052800'),('a'*40,'a'*40,'now'),('a'*40,'a'*40,'100000000')]:
   with self.subTest(args=args),self.assertRaises(ValueError):b.plan(*args)
 def test_build_info_explicitly_uses_same_timestamp(self):
  root=Path(__file__).resolve().parents[2];pom=ET.parse(root/'pom.xml');ns={'m':'http://maven.apache.org/POM/4.0.0'}
  plugins=pom.findall('.//m:build/m:plugins/m:plugin',ns)
  spring=next(p for p in plugins if p.find('m:artifactId',ns).text=='spring-boot-maven-plugin')
  self.assertEqual(spring.find('.//m:configuration/m:time',ns).text,'${project.build.outputTimestamp}')
  enforcer=next(p for p in plugins if p.find('m:artifactId',ns).text=='maven-enforcer-plugin')
  self.assertEqual(enforcer.find('.//m:requireProperty/m:property',ns).text,'project.build.outputTimestamp')
 def test_preparation_review_ci_share_same_entrypoint(self):
  root=Path(__file__).resolve().parents[2]
  for f in ['java-release.yml','pr.yml']:
   text=(root/'.github/workflows'/f).read_text()
   self.assertIn('python scripts/deployment/reproducible_build.py --commit "$GITHUB_SHA"',text)
   self.assertNotIn('mvn clean verify',text)
 def test_real_entrypoint_passes_immutable_timestamp_and_exact_versions(self):
  pins=(Path(__file__).resolve().parents[2]/'mise.toml').read_text()
  outputs=['OpenJDK Runtime Environment Corretto-25.0.5.0.10 (build 25.0.5+10-LTS)','Apache Maven 3.9.16','v24.22.2','10.34.7','']
  with patch('sys.argv',['reproducible_build.py','--commit','a'*40]),patch.object(b,'output',side_effect=['a'*40,'','','1794052800']),patch.object(Path,'read_text',return_value=pins),patch.object(b.subprocess,'run',side_effect=[SimpleNamespace(stdout=x,stderr='') for x in outputs]) as run,patch.object(b,'verify_factories_timestamp') as verified:
   b.main()
   self.assertEqual(run.call_count,5)
   verified.assert_called_once_with(Path('target/bibleverse-2.0.0-aws.jar'),b.timestamp('1794052800'))
   call=run.call_args
   self.assertEqual(call.args[0],b.plan('a'*40,'a'*40,'1794052800')['command'])
   self.assertEqual(call.kwargs['env']['SOURCE_DATE_EPOCH'],'1794052800')
   self.assertEqual(call.kwargs['env']['TZ'],'UTC')
   self.assertIn('-Djava.properties.date='+b.timestamp('1794052800'),call.kwargs['env']['MAVEN_OPTS'])

 def test_untracked_and_ignored_build_inputs_block(self):
  for path in ['src/main/java/Injected.java','src/main/resources/extra.properties','.mvn/maven.config','.mvn/extensions.xml','.mvn/jvm.config','pom.xml.backup','ignored-source/extra.java']:
   with self.subTest(path=path),self.assertRaises(ValueError):b.check_untracked([path])
  b.check_untracked(['target/stale.jar','.private/review.json','.infrastructure/node_modules/pkg/index.js','scripts/deployment/__pycache__/test.cpython-312.pyc'])
 def test_ignored_maven_configuration_rejected_before_tools_or_build(self):
  with patch('sys.argv',['reproducible_build.py','--commit','a'*40]),patch.object(b,'output',side_effect=['a'*40,'','.mvn/extensions.xml\0']),patch.object(b.subprocess,'run') as run:
   with self.assertRaisesRegex(ValueError,'build input'):b.main()
   run.assert_not_called()

 def test_properties_date_is_startup_option_from_commit_not_maven_property(self):
  stamp=b.timestamp('1794052800');env=b.build_environment({'MAVEN_OPTS':'-Xmx1g','OTHER':'kept'},'1794052800',stamp)
  self.assertEqual(env['MAVEN_OPTS'],'-Xmx1g -Djava.properties.date='+stamp)
  self.assertEqual(env['OTHER'],'kept')
  self.assertFalse(any('java.properties.date' in x for x in b.plan('a'*40,'a'*40,'1794052800')['command']))
  with self.assertRaises(ValueError):b.build_environment({},'1794052800','2026-11-07T00:00:00Z')
 def test_conflicting_jvm_date_override_rejected(self):
  for key in ['MAVEN_OPTS','JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS']:
   with self.subTest(key=key),self.assertRaises(ValueError):b.build_environment({key:'-Djava.properties.date=other'},'1794052800',b.timestamp('1794052800'))

 def test_echoing_options_block_before_java_without_leaking_values(self):
  sentinel='PRIVATE_SENTINEL_654321'
  for key in ['JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS']:
   for print_plan in [False,True]:
    with self.subTest(key=key,print_plan=print_plan),patch.dict(b.os.environ,{key:sentinel},clear=True),patch('sys.argv',['reproducible_build.py','--commit','a'*40]+(['--print-plan'] if print_plan else [])),patch.object(b,'output',side_effect=['a'*40,'','','1794052800']),patch.object(b.subprocess,'run') as run,contextlib.redirect_stdout(io.StringIO()) as stdout,contextlib.redirect_stderr(io.StringIO()) as stderr:
     with self.assertRaises(ValueError) as error:b.main()
     run.assert_not_called()
     self.assertNotIn(sentinel,str(error.exception)+stdout.getvalue()+stderr.getvalue())
 def test_print_plan_never_serializes_inherited_maven_options(self):
  sentinel='PRIVATE_SENTINEL_654321'
  for value in ['-Dprivate='+sentinel,'-Djava.properties.date='+sentinel]:
   with patch.dict(b.os.environ,{'MAVEN_OPTS':value},clear=True),patch('sys.argv',['reproducible_build.py','--commit','a'*40,'--print-plan']),patch.object(b,'output',side_effect=['a'*40,'','','1794052800']),patch.object(b.subprocess,'run') as run,contextlib.redirect_stdout(io.StringIO()) as stdout,contextlib.redirect_stderr(io.StringIO()) as stderr:
    if 'java.properties.date' in value:
     with self.assertRaises(ValueError) as error:b.main()
     self.assertNotIn(sentinel,str(error.exception))
    else:b.main()
    run.assert_not_called()
    self.assertNotIn(sentinel,stdout.getvalue()+stderr.getvalue())

 def test_actual_jar_header_proves_startup_property_effect(self):
  stamp=b.timestamp('1794052800')
  with tempfile.TemporaryDirectory() as td:
   jar=Path(td)/'test.jar'
   for header in ['#'+stamp,'#current wallclock','#other']:
    with zipfile.ZipFile(jar,'w') as z:z.writestr('META-INF/spring.factories','#Merged by PropertiesMergingResourceTransformer\n'+header+'\nkey=value\n')
    if header=='#'+stamp:b.verify_factories_timestamp(jar,stamp)
    else:
     with self.assertRaisesRegex(ValueError,'JVM did not apply'):b.verify_factories_timestamp(jar,stamp)
   with zipfile.ZipFile(jar,'w') as z:z.writestr('other','value')
   with self.assertRaises(ValueError):b.verify_factories_timestamp(jar,stamp)

 def test_wrong_toolchain_blocks_before_maven_build(self):
  with patch('sys.argv',['reproducible_build.py','--commit','a'*40]),patch.object(b,'output',side_effect=['a'*40,'','','1794052800']),patch.object(Path,'read_text',return_value='[tools]\njava="corretto-25"\nmaven="3.9"\nnode="24"\npnpm="10.34.7"'),patch.object(b.subprocess,'run') as run:
   with self.assertRaises(ValueError):b.main()
   run.assert_not_called()

if __name__=='__main__':unittest.main()
