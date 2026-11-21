"""tools/m27-contract/hooks: pre-commit and commit-msg refuse private names, home folders and internal wording."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'
WORD = 'teststand'          # a fixture word for the wording list, nothing private
NAME = 'zz-private-fixture'  # a fixture private name


class HygieneHooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        (d / 'names').write_text('fixture name: %s\n' % NAME)
        (d / 'patterns').write_text('# comment\nfixture pattern\tsecret-[0-9]+\n')
        (d / 'wording').write_text('test stand\t\\b%s\\b\n' % WORD)
        self.lists = d / 'lists'
        self.lists.write_text('names %s\npatterns %s\nwording %s\n' % (d / 'names', d / 'patterns', d / 'wording'))
        self.repo = d / 'repo'
        self.env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e',
                        GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
        self.env.pop('GIT_DIR', None); self.env.pop('M27_HYGIENE_LISTS', None)
        self.git('init', '-q', str(self.repo))
        r = subprocess.run(['sh', str(HOOKS / 'install'), '--lists', str(self.lists), str(self.repo)], capture_output=True, text=True, env=self.env)
        self.assertEqual(0, r.returncode, r.stderr)

    def git(self, *args, check=True):
        return subprocess.run(['git', *args], cwd=self.repo if self.repo.exists() else None, capture_output=True,
                              text=True, env=self.env, check=check)

    def commit(self, path, text, message):
        f = self.repo / path; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text)
        self.git('add', path)
        return self.git('commit', '-q', '-m', message, check=False)

    def test_clean_commit_passes(self):
        r = self.commit('patches/x/README.md', 'Laptop test 2026-11-14, image abc (26B131)\n', 'x: what and why\n\nTested: y')
        self.assertEqual(0, r.returncode, r.stderr)

    def test_message_lengths(self):
        ok = 'x: short subject\n\nWhy.\n\nTested: suite OK'
        self.assertEqual(0, self.commit('a.txt', 'one\n', ok).returncode)
        for bad, why in (('s' * 73 + '\n\nTested: OK', 'subject'), ('x\n\n' + 'l\n' * 7 + '\nTested: OK', 'body'),
                         ('x\n\nTested: ' + 'y' * 160, 'Tested'), ('x\n\nno tested line', 'no Tested')):
            r = self.commit('a.txt', 'two %d\n' % len(bad), bad)
            self.assertNotEqual(0, r.returncode, why); self.assertIn(why, r.stderr)
            self.git('reset', '-q')

    def test_message_with_a_private_name_or_wording_is_refused(self):
        for message in ('fix %s thing' % NAME, 'seen on the %s' % WORD, 'value secret-42', 'in /' 'Users/someone/x'):
            r = self.commit('a.txt', 'clean %d\n' % len(message), message + '\n\nTested: OK')
            self.assertNotEqual(0, r.returncode, message)
            self.assertIn('hygiene: commit message names a', r.stderr)
            self.assertNotIn(message, r.stderr)                      # the label, never the matched text
            self.git('reset', '-q')

    def test_added_lines_are_checked_wording_only_in_docs(self):
        r = self.commit('patches/x/README.md', 'proven on the %s\n' % WORD, 'docs\n\nTested: OK')
        self.assertNotEqual(0, r.returncode); self.assertIn('patches/x/README.md:1 names a test stand', r.stderr)
        self.git('reset', '-q')
        r = self.commit('src/tool.py', 'mode = "%s"\n' % WORD, 'code\n\nTested: OK')           # wording in code is not checked
        self.assertEqual(0, r.returncode, r.stderr)
        r = self.commit('src/tool.py', 'mode = "%s"\npath = "/' 'Users/someone/x"\n' % WORD, 'code 2\n\nTested: OK')
        self.assertIn('src/tool.py:2 names a home folder', r.stderr)

    def test_install_keeps_a_foreign_hook_and_the_push_guard(self):
        hooks = self.repo / '.git' / 'hooks'
        (hooks / 'pre-push').write_text('#!/bin/sh\n# m27 push guard\nexit 1\n')
        (hooks / 'commit-msg').write_text('#!/bin/sh\n# someone else\n')
        r = subprocess.run(['sh', str(HOOKS / 'install'), '--lists', str(self.lists), str(self.repo)], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(0, r.returncode); self.assertIn('not the hygiene hook', r.stderr)
        self.assertIn('m27 push guard', (hooks / 'pre-push').read_text())
        self.assertIn('someone else', (hooks / 'commit-msg').read_text())


if __name__ == '__main__':
    unittest.main()
