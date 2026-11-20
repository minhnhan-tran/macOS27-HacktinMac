"""A user's checkout run under sudo (create-installer): make-on-mac runs macos27tool-runtime's tool as the user who ran sudo,
and git reads the checkout although root does not own it."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

TOP = Path(__file__).resolve().parents[2]
M = runpy.run_path(str(TOP / 'pipeline' / 'make-on-mac'))
S = runpy.run_path(str(TOP / 'pipeline' / 'source_commit.py'))
G = M['runtime_tool'].__globals__          # the functions' own globals (run_path returns a copy)


class FakeRun:
    def __init__(self, work, dry=False):
        self.work, self.dry, self.calls = Path(work), dry, []

    def sh(self, *cmd, env=None, user=None):
        self.calls.append((list(cmd), env, user))


class RuntimeTool(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ['rm', '-rf', str(self.d)])
        self.top, self.view = self.d / 'macos27tool-runtime', self.d / 'work' / 'root-view'
        (self.top / 'payload').mkdir(parents=True)
        os.chmod(self.top / 'payload', 0o777)
        (self.view / 'usr/libexec').mkdir(parents=True)
        (self.view / 'usr/libexec/trampoline').write_bytes(b'x')
        os.chmod(self.view / 'usr/libexec/trampoline', 0o111)     # execute-only, as the System image has it
        self.r = FakeRun(self.d / 'work')

    def test_ordinary_user_runs_the_tool_as_today(self):
        M['runtime_tool'](self.r, 'tool', self.view, 'x86', self.top, euid=501, environ={'SUDO_UID': '501'})
        self.assertEqual(self.r.calls, [(M['PY'] + ['tool', self.view, 'x86', self.top], None, None)])

    def test_root_without_sudo_uid_refuses(self):
        for environ in ({}, {'SUDO_UID': '0', 'SUDO_GID': '0'}, {'SUDO_UID': 'x', 'SUDO_GID': '20'}):
            with self.assertRaisesRegex(M['Failed'], 'ordinary user'):
                M['runtime_tool'](self.r, 'tool', self.view, 'x86', self.top, euid=0, environ=environ)
        self.assertEqual(self.r.calls, [])

    def test_root_under_sudo_runs_the_tool_as_that_user(self):
        uid, gid = os.getuid(), os.getgid()                     # chown to oneself needs no privilege
        seen = []
        with mock.patch.dict(G, user_tmp=lambda who: seen.append(who) or '/private/var/folders/xx/T'):
            M['runtime_tool'](self.r, 'tool', self.view, 'x86', self.top, euid=0,
                              environ={'SUDO_UID': str(uid), 'SUDO_GID': str(gid)})
        [(cmd, env, user)] = self.r.calls
        self.assertEqual(cmd, M['PY'] + ['tool', self.view, 'x86', self.top])
        self.assertEqual(user[:2], (uid, gid))
        self.assertEqual(seen, [user])
        self.assertEqual(env['TMPDIR'], '/private/var/folders/xx/T')
        self.assertEqual(env['HOME'], os.path.expanduser('~'))
        self.assertEqual(stat.S_IMODE(os.stat(self.top / 'payload').st_mode), 0o777)  # a row's modes are kept
        self.assertTrue(os.stat(self.view / 'usr/libexec/trampoline').st_mode & 0o400)    # readable by the user

    def test_dry_run_changes_nothing(self):
        r = FakeRun(self.d / 'work', dry=True)
        M['runtime_tool'](r, 'tool', self.view, 'x86', self.top, euid=0,
                          environ={'SUDO_UID': str(os.getuid()), 'SUDO_GID': str(os.getgid())})
        self.assertEqual(stat.S_IMODE(os.stat(self.top / 'payload').st_mode), 0o777)
        self.assertEqual(r.calls[0][2][0], os.getuid())

    def test_sh_switches_user_in_the_child(self):
        with tempfile.TemporaryFile('w+') as log, mock.patch('subprocess.run') as run, \
                contextlib.redirect_stdout(io.StringIO()):
            run.return_value = subprocess.CompletedProcess([], 0)
            M['Run']([], self.d, log, False).sh('tool', user=(502, 20, [20, 12]), env={'TMPDIR': '/t'})
        kw = run.call_args.kwargs
        self.assertEqual((kw['user'], kw['group'], kw['extra_groups']), (502, 20, [20, 12]))
        self.assertEqual(kw['env']['TMPDIR'], '/t')

    def test_an_admin_users_groups_fit_setgroups(self):
        many = [20, 12, 61, 79, 80, 81, 701, 702, 33, 98, 100, 204, 250, 395, 398, 399, 400]   # an admin, 17
        groups = M['user_groups']('tester', 20, grouplist=lambda name, gid: many, limit=16)
        self.assertEqual(len(groups), 16)
        self.assertEqual(groups[0], 20)
        self.assertEqual(M['user_groups']('u', 20, grouplist=lambda n, g: [12, 20]), [20, 12])
        self.assertLessEqual(len(M['sudo_user']({'SUDO_UID': str(os.getuid()), 'SUDO_GID': str(os.getgid())})[2]),
                             os.sysconf('SC_NGROUPS_MAX'))

    def test_work_folder_lets_only_that_user_through(self):
        import pwd
        name = pwd.getpwuid(os.getuid()).pw_name
        M['let_in'](self.r.work, os.getuid(), name)                 # already theirs: unchanged
        self.assertNotIn('allow search', subprocess.run(['ls', '-led', str(self.r.work)], capture_output=True,
                                                        text=True).stdout)
        M['let_in'](self.r.work, os.getuid() + 1, name)             # root's: an ACL entry for that user only
        acl = subprocess.run(['ls', '-led', str(self.r.work)], capture_output=True, text=True).stdout
        self.assertIn('user:%s allow search' % name, acl)

    def test_user_tmp_must_be_private(self):
        private = self.d / 'T'
        private.mkdir(mode=0o700)
        os.chmod(private, 0o700)
        who = (os.getuid(), os.getgid(), [])
        with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, str(private) + '/\n')) as run:
            self.assertEqual(M['user_tmp'](who), str(private) + '/')
            self.assertEqual(run.call_args.kwargs['user'], who[0])
            os.chmod(private, 0o777)
            with self.assertRaisesRegex(M['Failed'], 'no private temporary folder'):
                M['user_tmp'](who)


class GitTrustsTheCheckout(unittest.TestCase):
    """GIT_TEST_ASSUME_DIFFERENT_OWNER makes git see another owner, as root sees a user's checkout."""

    def setUp(self):
        if not (TOP / '.git').exists():
            self.skipTest('not a checkout')

    def test_git_env_is_this_checkout_only_and_added_once(self):
        add = S['git_env']({}, TOP)
        self.assertEqual(add, {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory',
                               'GIT_CONFIG_VALUE_0': str(TOP)})
        self.assertEqual(S['git_env'](add, TOP), {})
        self.assertEqual(S['git_env']({'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'a.b', 'GIT_CONFIG_VALUE_0': 'c'},
                                      TOP)['GIT_CONFIG_KEY_1'], 'safe.directory')
        other = {k: v for k, v in os.environ.items() if not k.startswith('GIT_CONFIG_')}   # a main() run in-process
        other['GIT_TEST_ASSUME_DIFFERENT_OWNER'] = '1'                                    # may have set them
        cmd = ['git', '-C', str(TOP), 'rev-parse', 'HEAD']
        self.assertNotEqual(subprocess.run(cmd, capture_output=True, env=other).returncode, 0)
        self.assertEqual(subprocess.run(cmd, capture_output=True, env=dict(other, **add)).returncode, 0)

    def test_source_commit_and_golden_run_source_identity(self):
        with mock.patch.dict(os.environ, GIT_TEST_ASSUME_DIFFERENT_OWNER='1', GIT_CONFIG_COUNT='0'):
            self.assertRegex(S['commit'](TOP), '^[0-9a-f]{40}$')
            G2 = runpy.run_path(str(TOP / 'pipeline' / 'golden-run'))
            self.assertRegex(G2['source_identity']()['commit'], '^[0-9a-f]{40}$')

    def test_golden_run_passes_safe_directory(self):
        G2 = runpy.run_path(str(TOP / 'pipeline' / 'golden-run'))
        with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'a' * 40)) as run:
            G2['source_identity']()
        for call in run.call_args_list:
            self.assertEqual(call.args[0][1:3], ['-c', 'safe.directory=' + str(TOP)])


if __name__ == '__main__':
    unittest.main()
