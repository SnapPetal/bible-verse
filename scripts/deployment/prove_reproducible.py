"""Nondeploy two-worktree proof. Raw build output stays in ephemeral private files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zipfile


def digest(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def need(ok,message):
    if not ok:raise ValueError(message)


def run(command,cwd,log):
    with log.open('ab') as stream:
        subprocess.run(command,cwd=cwd,stdout=stream,stderr=subprocess.STDOUT,check=True,
                       env={**os.environ,'AWS_EC2_METADATA_DISABLED':'true','CDK_DISABLE_VERSION_CHECK':'1'})


def snapshot(worktree):
    jar=worktree/'target/bibleverse-2.0.0-aws.jar';raw=jar.read_bytes()
    template=worktree/'.private/cdk-source/bible-verse-stack.template.json'
    manifest_paths=sorted((worktree/'.private/cdk-source').glob('*.assets.json'))
    need(manifest_paths,'Missing asset manifests')
    manifests={p.name:json.loads(p.read_text()) for p in manifest_paths}
    for value in manifests.values():
        for asset in value.get('files',{}).values():
            path=asset.get('source',{}).get('path')
            need(type(path) is str and not Path(path).is_absolute() and '..' not in Path(path).parts,'Nonportable asset source path')
            need((worktree/'.private/cdk-source'/path).exists(),'Missing staged asset source')
    with zipfile.ZipFile(jar) as z:
        entries=[{'path_sha256':digest(i.filename.encode()),'bytes_sha256':digest(z.read(i)),
                  'timestamp':i.date_time,'compression':i.compress_type,'attributes':i.external_attr,
                  'extra_sha256':digest(i.extra),'comment_sha256':digest(i.comment)} for i in z.infolist()]
    return {'jar_sha256':digest(raw),'jar_bytes':len(raw),'template_sha256':digest(canonical(json.loads(template.read_text()))),
            'asset_manifests_sha256':digest(canonical(manifests)),'entries':entries}


def entry_differences(first,second,limit=50):
    # Include ordinal so duplicate ZIP names and ordering changes remain visible.
    def indexed(entries):
        seen={};result={}
        for ordinal,entry in enumerate(entries):
            path=entry['path_sha256'];occurrence=seen.get(path,0);seen[path]=occurrence+1
            metadata={k:v for k,v in entry.items() if k not in ['path_sha256','bytes_sha256']}
            result[(path,occurrence)]={'content_sha256':entry['bytes_sha256'],
                                     'metadata_sha256':digest(canonical({'ordinal':ordinal,**metadata}))}
        return result
    before=indexed(first);after=indexed(second);changed=[]
    for path,occurrence in sorted(before.keys()|after.keys()):
        old=before.get((path,occurrence));new=after.get((path,occurrence))
        if old!=new:changed.append({'path_sha256':path,'occurrence':occurrence,'before':old,'after':new})
    return {'changed_entry_count':len(changed),'entry_differences':changed[:limit],
            'entry_differences_truncated':len(changed)>limit,'entry_differences_complete':len(changed)<=limit}


def compare(first,second):
    keys=['jar_sha256','jar_bytes','template_sha256','asset_manifests_sha256']
    differences=[key for key in keys if first[key]!=second[key]]
    result={'passed':not differences,**{key:first[key] for key in keys},'differing_hash_fields':differences}
    if differences:
        # Hashed entry paths/metadata only; never raw ZIP contents or template values.
        result['zip_entry_metadata_sha256']=[digest(canonical(x['entries'])) for x in [first,second]]
        result['zip_entry_count']=[len(x['entries']) for x in [first,second]]
        result.update(entry_differences(first['entries'],second['entries']))
    return result


def prove(repo,commit,runner=run,sleep=time.sleep):
    need(re.fullmatch('[a-f0-9]{40}',commit) is not None,'Exact reviewed commit required')
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,check=True,capture_output=True,text=True).stdout.strip()
    need(head==commit,'Checkout differs from reviewed commit')
    # No credentials are required or accepted for this proof.
    need(not any(os.environ.get(k) for k in ['AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN','AWS_WEB_IDENTITY_TOKEN_FILE']),'AWS credentials present')
    with tempfile.TemporaryDirectory(prefix='bible-proof-') as td:
        root=Path(td);results=[]
        for n in [1,2]:
            worktree=root/f'build-{n}';log=root/f'build-{n}.private.log'
            runner(['git','worktree','add','--detach',str(worktree),commit],repo,log)
            runner(['python','scripts/deployment/reproducible_build.py','--commit',commit],worktree,log)
            runner(['pnpm','install','--frozen-lockfile'],worktree/'.infrastructure',log)
            runner(['pnpm','exec','cdk','synth','bible-verse-stack','--app','pnpm exec ts-node bin/bibleverse-source-only.ts','--no-lookups','--output','../.private/cdk-source','--quiet'],worktree/'.infrastructure',log)
            results.append(snapshot(worktree))
            if n==1:sleep(3)
        result=compare(*results);result['source_commit']=commit
        # Local ephemeral worktrees only; no remote/network state is changed.
        for n in [1,2]:runner(['git','worktree','remove','--force',str(root/f'build-{n}')],repo,root/'cleanup.private.log')
        return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--commit',required=True);args=parser.parse_args()
    try:
        result=prove(Path.cwd(),args.commit);print(json.dumps(result,sort_keys=True))
        if not result['passed']:raise SystemExit(1)
    except (ValueError,OSError,subprocess.SubprocessError,zipfile.BadZipFile):
        print('Reproducibility proof failed; no raw build output or template values emitted.')
        raise SystemExit(1)


if __name__=='__main__':main()
