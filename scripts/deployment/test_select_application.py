import unittest
import select_application as a

class SelectApplication(unittest.TestCase):
    def test_default_uses_authenticated_head(self):
        self.assertEqual(a.validate('', 'a'*40, 'push', True, []), 'a'*40)

    def test_manual_same_application_allows_only_exact_helper_diffs(self):
        self.assertEqual(a.validate('b'*40, 'a'*40, 'workflow_dispatch', True, list(a.ALLOWED_DIFFS)), 'b'*40)

    def test_rejects_nonancestor_push_and_unbounded_paths(self):
        for event, ancestor, changed in [('push', True, []), ('workflow_dispatch', False, []),
            ('workflow_dispatch', True, ['pom.xml']), ('workflow_dispatch', True, ['mise.toml']),
            ('workflow_dispatch', True, ['scripts/deployment/reproducible_build.py']),
            ('workflow_dispatch', True, ['.infrastructure/lib/bibleverse-stack.ts']),
            ('workflow_dispatch', True, ['src/main/resources/application.properties'])]:
            with self.subTest(event=event, changed=changed), self.assertRaises(ValueError):
                a.validate('b'*40, 'a'*40, event, ancestor, changed)

    def test_rejects_nonexact_sources(self):
        for value in ['main', 'abc123', '--help', 'a'*39, 'A'*40]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                a.validate(value, 'b'*40, 'workflow_dispatch', True, [])

    def test_clean_worktree_uses_original_commit_and_timestamp(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git('init')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture')
            (root / 'pom.xml').write_text('same build inputs')
            git('add', '.')
            git('commit', '-m', 'application')
            source = git('rev-parse', 'HEAD')
            stamp = git('show', '-s', '--format=%ct', source)
            helper = root / 'scripts/deployment/simple_deploy.py'
            helper.parent.mkdir(parents=True)
            helper.write_text('new deployment only')
            git('add', '.')
            git('commit', '-m', 'workflow')
            head = git('rev-parse', 'HEAD')
            event = root / '.private/event.json'
            event.parent.mkdir()
            event.write_text(json.dumps({'inputs': {'application_commit': source}}))
            env = {'GITHUB_SHA': head, 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_EVENT_PATH': str(event)}
            with patch.object(a, 'ROOT', root), patch.dict(os.environ, env):
                app, actual = a.select(prepare=True)
                self.assertEqual(actual, source)
                self.assertEqual(a.git('show', '-s', '--format=%ct', 'HEAD', cwd=app), stamp)
                self.assertEqual(git('rev-parse', 'HEAD'), head)
                self.assertEqual(a.select(), (app, source))
                (app / 'pom.xml').write_text('changed build')
                with self.assertRaises(ValueError):
                    a.select()
