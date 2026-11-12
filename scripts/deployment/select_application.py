"""Select a clean ancestor application build for a manual ordinary phase-two retry.

Deployment code still runs from authenticated main. No AWS operations or deployment.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_DIFFS = frozenset({
    '.github/workflows/java-release.yml',
    'scripts/deployment/simple_deploy.py',
    'scripts/deployment/test_simple_deploy.py',
    'scripts/deployment/select_application.py',
    'scripts/deployment/test_select_application.py',
    'scripts/deployment/test_private_records.py',
})


def need(ok, message):
    if not ok:
        raise ValueError(message)


def git(*args, cwd=None):
    return subprocess.check_output(['git', *args], cwd=ROOT if cwd is None else cwd, text=True).strip()


def validate(source, head, event_name, ancestor, changed):
    need(re.fullmatch('[a-f0-9]{40}', head) is not None, 'Exact workflow source required')
    if not source:
        return head
    need(event_name == 'workflow_dispatch' and re.fullmatch('[a-f0-9]{40}', source) is not None,
         'Pinned application requires exact manual source')
    need(ancestor and set(changed) <= ALLOWED_DIFFS, 'Application or build inputs differ from pinned source')
    return source


def select(prepare=False):
    head = os.environ.get('GITHUB_SHA', '')
    need(git('rev-parse', 'HEAD') == head, 'Workflow checkout differs')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    source = event.get('inputs', {}).get('application_commit', '')
    ancestor, changed = True, []
    if source:
        need(re.fullmatch('[a-f0-9]{40}', source) is not None, 'Exact application commit required')
        ancestor = subprocess.run(['git', 'merge-base', '--is-ancestor', source, head], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        changed = git('diff', '--name-only', source, head, '--').splitlines()
    commit = validate(source, head, os.environ.get('GITHUB_EVENT_NAME'), ancestor, changed)
    root = ROOT if commit == head else ROOT / '.private/application'
    if prepare and root != ROOT:
        need(not root.exists(), 'Application checkout already exists')
        root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'worktree', 'add', '--detach', str(root), commit], cwd=ROOT, check=True)
    need(root.is_dir() and git('rev-parse', 'HEAD', cwd=root) == commit, 'Pinned application checkout differs')
    need(not git('status', '--porcelain', '--untracked-files=no', cwd=root), 'Application checkout has tracked edits')
    return root, commit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    root, commit = select(args.prepare)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('root=' + str(root) + '\ncommit=' + commit + '\n')


if __name__ == '__main__':
    main()

