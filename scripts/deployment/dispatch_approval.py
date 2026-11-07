"""Local candidate: per-dispatch disclosure approval; no AWS calls or writes."""
import hashlib
import json
import os
import re
import subprocess
import bible_deploy as d

REPO='SnapPetal/bible-verse'
WORKFLOW='.github/workflows/java-release.yml'
PIN_KEYS={'source_commit','plan_sha256','baseline_sha256','candidate_sha256','assets_sha256','phase1_sha256','phase2_sha256'}
DISCLOSURE='private-reviewed-deployment-records-and-public-bounded-references-v1'


def github(path):
    return json.loads(subprocess.run(['gh','api',path],check=True,capture_output=True,text=True).stdout)


def validate(config,event,env,read,record=None):
    d.need(env.get('GITHUB_REPOSITORY')==REPO and env.get('GITHUB_REF')=='refs/heads/main' and
           env.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and
           env.get('GITHUB_WORKFLOW_REF')==REPO+'/'+WORKFLOW+'@refs/heads/main','Wrong approval source context')
    inputs=event.get('inputs',{})
    for key in ['approve_private_records','approve_public_references']:
        value=inputs.get(key)
        d.need(value is True or (type(value) is str and value=='true'),'Explicit dispatch disclosure approval required')
    mode=inputs.get('mode');d.need(mode in ['review','release','operator-reconcile'],'Wrong approval mode')
    pins=json.loads(inputs.get('review_pins','{}'))
    d.need(set(pins)==PIN_KEYS,'Incomplete exact review pins')
    for key,value in pins.items():
        d.need(type(value) is str and re.fullmatch('[a-f0-9]{'+('40' if key=='source_commit' else '64')+'}',value),'Invalid review pin')
    d.need(pins['source_commit']==env.get('GITHUB_SHA'),'Approval source mismatch')
    # The authenticated dispatch is the per-run consent record, not a committed
    # per-release registry. Existing artifact/plan gates still verify package bytes.
    binding={'disclosure':DISCLOSURE,'pins':pins,'mode':mode,
             'bundle_ref':json.loads(inputs.get('bundle_ref') or 'null'),
             'review_artifact_id':inputs.get('review_artifact_id') or '',
             'review_artifact_digest':inputs.get('review_artifact_digest') or ''}
    if mode=='review':
        import private_records
        private_records.validate_reference(binding['bundle_ref'])
        d.need(not binding['review_artifact_id'] and not binding['review_artifact_digest'],'Mixed review sources')
    else:
        d.need(binding['bundle_ref'] is None and re.fullmatch('[1-9][0-9]*',binding['review_artifact_id']) and
               re.fullmatch('sha256:[a-f0-9]{64}',binding['review_artifact_digest']),'Missing exact reviewed artifact')
    actor=env.get('GITHUB_ACTOR_ID','');run_id=env.get('GITHUB_RUN_ID','')
    d.need(re.fullmatch('[1-9][0-9]*',actor) and re.fullmatch('[1-9][0-9]*',run_id),'Invalid actor/run')
    allowed=config.get('java_operator_actor_ids' if mode=='operator-reconcile' else 'java_release_actor_ids',[])
    d.need(int(actor) in allowed and event.get('sender',{}).get('id')==int(actor),'Unapproved dispatch actor')
    run=read(f'repos/{REPO}/actions/runs/{run_id}')
    d.need(run.get('id')==int(run_id) and run.get('repository',{}).get('full_name')==REPO and
           run.get('path')==WORKFLOW and run.get('head_sha')==pins['source_commit'] and
           run.get('head_branch')=='main' and run.get('event')=='workflow_dispatch' and
           run.get('run_attempt')==int(env.get('GITHUB_RUN_ATTEMPT','0')) and
           run.get('actor',{}).get('id')==int(actor) and run.get('triggering_actor',{}).get('id')==int(actor),
           'Unverified GitHub run provenance')
    d.need(read(f'repos/{REPO}/git/ref/heads/main').get('object',{}).get('sha')==pins['source_commit'],'Main changed')
    request_digest=hashlib.sha256(json.dumps({'run_id':run_id,'run_attempt':run['run_attempt'],'actor_id':int(actor),'inputs':inputs},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if record is not None:
        d.need(record.get('dispatch_approval_sha256')==request_digest,'Record dispatch approval changed')
        kinds={'review':{'java-review'},'release':{'java-intent','java-response'},'operator-reconcile':{'java-operator-evidence'}}
        d.need(record.get('kind') in kinds[mode],'Record kind outside approved mode')
        d.need(record.get('source_commit')==pins['source_commit'] and record.get('plan_sha256')==pins['plan_sha256'] and
               str(record.get('run_id'))==run_id,'Record outside approved package/run')
    return {**binding, 'request_sha256':request_digest}


def authorize(config,record=None):
    return validate(config,d.load(os.environ['GITHUB_EVENT_PATH']),os.environ,github,record)
