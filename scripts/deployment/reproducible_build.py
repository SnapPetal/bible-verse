"""Same immutable source timestamp for preparation and review. No installer/network API."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile


def require(ok,message):
    if not ok:raise ValueError(message)


def timestamp(epoch):
    require(type(epoch) is str and re.fullmatch('[1-9][0-9]{8,10}',epoch),'Invalid commit timestamp')
    value=datetime.datetime.fromtimestamp(int(epoch),datetime.timezone.utc)
    require(1980<=value.year<=2107,'Commit timestamp outside ZIP range')
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def plan(commit,head,epoch,dirty=False):
    require(type(commit) is str and re.fullmatch('[a-f0-9]{40}',commit),'Exact source commit required')
    require(commit==head and not dirty,'Source differs from frozen commit')
    stamp=timestamp(epoch)
    return {'source_commit':commit,'source_date_epoch':epoch,'output_timestamp':stamp,
            'command':['mvn','--batch-mode','--no-transfer-progress','clean','verify','-Dproject.build.outputTimestamp='+stamp]}


def output(command):
    return subprocess.run(command,check=True,capture_output=True,text=True).stdout.strip()


def check_untracked(paths):
    # Include ignored files: .gitignore is not authorization to consume an input.
    # Only known generated output/cache locations are exempt; Maven cleans target.
    output_roots=('target/','.private/','.infrastructure/node_modules/','.infrastructure/cdk.out/')
    for path in paths:
        require(type(path) is str and not path.startswith('/') and '..' not in path.split('/'),'Invalid untracked path')
        bytecode=re.fullmatch(r'scripts/deployment/__pycache__/[A-Za-z0-9_.-]+\.pyc',path)
        require(path.startswith(output_roots) or bytecode is not None,'Untracked or ignored build input: '+path)


def build_environment(base,epoch,stamp):
    require(timestamp(epoch)==stamp,'Properties date must equal immutable commit timestamp')
    for key in ['JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS']:
        require(not base.get(key,''),'Echoing JVM options are not permitted')
    for key in ['MAVEN_OPTS']:
        require('java.properties.date' not in base.get(key,''),'Conflicting Properties date override')
    # Properties caches this at JVM startup; a Maven -D user property is too late.
    opts=(base.get('MAVEN_OPTS','').strip()+' -Djava.properties.date='+stamp).strip()
    return {**base,'SOURCE_DATE_EPOCH':epoch,'TZ':'UTC','LC_ALL':'C','LANG':'C','MAVEN_OPTS':opts}


def verify_factories_timestamp(jar,stamp):
    with zipfile.ZipFile(jar) as archive:
        entries=[x for x in archive.infolist() if x.filename=='META-INF/spring.factories']
        require(len(entries)==1,'Missing or duplicate spring.factories output')
        lines=archive.read(entries[0]).splitlines()
    require(len(lines)>=2 and lines[0]==b'#Merged by PropertiesMergingResourceTransformer' and
            lines[1]==('#'+stamp).encode('ascii'),'Maven JVM did not apply fixed Properties date')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--commit',required=True);parser.add_argument('--print-plan',action='store_true');args=parser.parse_args()
    head=output(['git','rev-parse','--verify','HEAD'])
    # Reject tracked edits and all non-output untracked inputs, including ignored ones.
    dirty=bool(output(['git','status','--porcelain','--untracked-files=no']))
    untracked=output(['git','ls-files','--others','-z'])
    check_untracked([p for p in untracked.split('\0') if p])
    epoch=output(['git','show','-s','--format=%ct',head])
    result=plan(args.commit,head,epoch,dirty)
    env=build_environment(os.environ,epoch,result['output_timestamp'])
    if args.print_plan:print(json.dumps(result,sort_keys=True));return
    pins=tomllib.loads(Path('mise.toml').read_text())['tools']
    require(set(pins)=={'java','maven','node','pnpm'},'Unexpected toolchain')
    require(re.fullmatch(r'corretto-\d+\.\d+\.\d+\.\d+\.\d+',pins['java']) is not None,'Exact Corretto build required')
    for key in ['maven','node','pnpm']:
        require(re.fullmatch(r'\d+\.\d+\.\d+',pins[key]) is not None,'Exact tool version required')
    checks=[(['java','-version'],r'Corretto-'+re.escape(pins['java'].removeprefix('corretto-'))+r'(?=\s|$)'),
            (['mvn','--version'],r'Apache Maven '+re.escape(pins['maven'])+r'(?=\s|$)'),
            (['node','--version'],r'^v'+re.escape(pins['node'])+r'\s*$'),
            (['pnpm','--version'],r'^'+re.escape(pins['pnpm'])+r'\s*$')]
    for command,pattern in checks:
        process=subprocess.run(command,check=True,capture_output=True,text=True)
        actual=process.stdout+process.stderr
        require(re.search(pattern,actual) is not None,'Wrong exact toolchain: '+command[0])
    subprocess.run(result['command'],check=True,env=env)
    verify_factories_timestamp(Path('target/bibleverse-2.0.0-aws.jar'),result['output_timestamp'])


if __name__=='__main__':main()
