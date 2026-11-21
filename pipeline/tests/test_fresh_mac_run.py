"""pipeline/fresh-mac-run with a fake VM: the share, the result read from a guest's output, the pass rule."""
from pathlib import Path
import json
import os
import runpy
import subprocess
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
F = runpy.run_path(str(TOP / 'pipeline' / 'fresh-mac-run'))

EVENTS = [
    dict(ev='run.start', t=0.0, text='macOS27Tool create-installer'),
    dict(ev='item', t=2.0, text='release.json signed'),
    dict(ev='step.start', t=10.0, step='Download', index=1, of=3),
    dict(ev='fetch.ok', t=40.0, name='OpenCore-1.0.5-RELEASE.zip'),
    dict(ev='step.start', t=100.0, step='Prepare packs', index=2, of=3),
    dict(ev='command', t=101.0, argv=['/py', '-I', '/x/pipeline/place', 'r.json', 'cache']),
    dict(ev='command', t=110.0, argv=['/py', '-I', '/x/pipeline/make-on-mac', 'r.json']),
    dict(ev='command', t=900.0, argv=['/py', '-I', '/x/pipeline/place', 'r.json', 'cache']),
    dict(ev='step.start', t=1000.0, step='Write the USB installer', index=3, of=3),
    dict(ev='stick.written', t=1290.0, stick={}),
    dict(ev='run.end', t=1300.0, ok=True, text='USB installer image written', seconds=1300.0),
]


def guest_output(out, events=EVENTS, rc=0, commit='c' * 40, stick='ab' * 32, backend=(), err=''):
    """What the guest job leaves in share/out."""
    out.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in events] + list(backend)
    (out / 'create-installer.jsonl').write_text('\n'.join(lines) + '\n')
    (out / 'create-installer.err').write_text(err)
    (out / 'create-installer.rc').write_text('%d\n' % rc)
    (out / 'commit').write_text(commit + '\n')
    (out / 'user.log').write_text('')
    (out / 'logs').mkdir(exist_ok=True)
    (out / 'stages.txt').write_text('100 job-start\n101 clone-start\n104 clone-end rc=0\n104 create-installer-start\n'
                                    '1404 create-installer-end rc=%d\n1404 stick-hash-start\n1500 stick-hash-end\n'
                                    '1502 job-end rc=%d\n' % (rc, rc))
    if stick:
        (out / 'stick.sha256').write_text(stick + '\n')
        (out / 'stick.stat').write_text('64000000000 62000000\n')
    (out / 'done').write_text('%d\n' % rc)


class Stages(unittest.TestCase):
    def test_steps_and_commands_from_the_event_log(self):
        stages = F['installer_stages'](EVENTS)
        self.assertEqual(stages, [['preflight and setup', 10.0], ['step: Download', 90.0],
                                  ['step: Prepare packs', 900.0], ['  place', 9.0], ['  make-on-mac', 790.0],
                                  ['  place', 100.0], ['step: Write the USB installer', 300.0]])

    def test_a_stopped_run_ends_at_its_last_event(self):
        events = EVENTS[:6] + [dict(ev='run.end', t=150.0, ok=False, what='making the inputs failed')]
        self.assertEqual(F['installer_stages'](events)[-2:], [['step: Prepare packs', 50.0], ['  place', 49.0]])

    def test_guest_stages(self):
        with tempfile.TemporaryDirectory() as d:
            guest_output(Path(d))
            self.assertEqual(F['guest_stages'](Path(d) / 'stages.txt'),
                             [['clone', 3, 0], ['create-installer', 1300, 0], ['stick-hash', 96, None],
                              ['job', 1402, 0]])


class Result(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.out = Path(self.d.name) / 'out'

    def tearDown(self):
        self.d.cleanup()

    def result(self, **kw):
        guest_output(self.out, **kw)
        r = F['collect'](self.out, dict(commit='c' * 40))
        return r, F['verdict'](r)

    def test_a_clean_run_passes(self):
        r, (passed, why) = self.result()
        self.assertTrue(passed, why)
        self.assertEqual(r['stick_sha256'], 'ab' * 32)
        self.assertEqual(r['stick_allocated_bytes'], 62000000 * 512)
        self.assertEqual(r['seconds'], 1402)
        self.assertEqual(r['mirror_misses'], [])
        self.assertEqual(r['rc'], 0)
        self.assertIn('make-on-mac', json.dumps(r['stages']['create_installer']))

    def test_backend_lines_between_events_are_kept_apart(self):
        r, (passed, _) = self.result(backend=['placed 41, already there 0'])
        self.assertTrue(passed)
        self.assertEqual(r['backend_lines'], 1)

    def test_a_mirror_miss_fails_the_run(self):
        r, (passed, why) = self.result(err='mirror miss UniversalMac_27.2.1_26B131_Restore.ipsw ' + '0' * 64 + '\n')
        self.assertFalse(passed)
        self.assertEqual(len(r['mirror_misses']), 1)
        self.assertIn('1 mirror misses', why)

    def test_a_failed_fetch_from_the_mirror_is_a_miss(self):
        events = EVENTS[:4] + [dict(ev='fetch.mirror-failed', t=41.0, name='x', url='u', error='404')] + EVENTS[4:]
        r, (passed, _) = self.result(events=events)
        self.assertFalse(passed)

    def test_a_nonzero_exit_fails(self):
        events = EVENTS[:5] + [dict(ev='run.end', t=150.0, ok=False, what='making the inputs failed', why='w',
                                    cause='c')]
        r, (passed, why) = self.result(events=events, rc=1, stick=None)
        self.assertFalse(passed)
        self.assertEqual(r['stopped']['what'], 'making the inputs failed')
        self.assertIn('create-installer exited 1', why)
        self.assertIn('no stick was written and hashed', why)

    def test_another_commit_fails(self):
        r, (passed, why) = self.result(commit='d' * 40)
        self.assertFalse(passed)

    def test_nothing_from_the_guest_fails(self):
        r = F['collect'](self.out, dict(commit='c' * 40))
        passed, why = F['verdict'](r)
        self.assertFalse(passed)
        self.assertIsNone(r['rc'])


def make_repo(path):
    path.mkdir()
    run = lambda *a: subprocess.run(['git', *a], cwd=path, check=True, capture_output=True)
    run('init', '-q')
    (path / 'f.txt').write_text('one\n')
    run('add', 'f.txt')
    run('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', 'one')
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=path, capture_output=True, text=True).stdout.strip()


def release_dir(path, signed=True):
    path.mkdir(parents=True)
    for name in F['RELEASE_FILES'] + (F['SIGNATURE_FILES'] if signed else ()):
        (path / name).write_text('%s\n' % name)
    (path / 'dss_shim.dylib').write_bytes(b'asset')
    return path


class Share(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name).resolve()

    def tearDown(self):
        self.d.cleanup()

    def test_the_share_holds_the_commit_the_release_and_the_key(self):
        repo = self.root / 'repo'
        commit = make_repo(repo)
        rel = release_dir(self.root / 'release')
        share = self.root / 'share'
        info = F['prepare_share'](share, rel, commit, repo, 'ssh-ed25519 AAAA candidate\n',
                                  dict(COMMIT=commit, MIRROR='http://192.168.64.1:8765', MODEL='MacBookPro9,2',
                                       GUSER='tester'))
        self.assertEqual(sorted(p.name for p in (share / 'in/release').iterdir()), sorted(F['RELEASE_FILES'] + F['SIGNATURE_FILES']))
        self.assertFalse((share / 'in/release/dss_shim.dylib').exists())      # assets come from the mirror
        self.assertEqual(info['release_sha256'], F['sha256'](rel / 'release.json'))
        self.assertEqual((share / 'in/candidate-keys.pub').read_text(), 'ssh-ed25519 AAAA candidate\n')
        params = (share / 'in/params').read_text()
        self.assertIn('COMMIT=%s\n' % commit, params)
        self.assertIn('MIRROR=http://192.168.64.1:8765\n', params)
        self.assertIn('MODEL=MacBookPro9,2\n', params)
        clone = self.root / 'clone'
        subprocess.run(['git', 'clone', '-q', str(share / 'in/hao-patcher.bundle'), str(clone)], check=True)
        self.assertEqual(subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=clone, capture_output=True,
                                        text=True).stdout.strip(), commit)
        self.assertTrue((share / 'out').is_dir())
        user = (share / 'in/user.sh').read_text()
        self.assertIn('sudo M27_MIRROR="$MIRROR" M27_MIRROR_STRICT=1 tools/macOS27Tool/macOS27Tool create-installer', user)
        self.assertIn('--log json', user)
        self.assertIn('su -l "$GUSER"', (share / 'in/job.sh').read_text())
        for script in ('user.sh', 'job.sh'):
            subprocess.run(['sh', '-n', str(share / 'in' / script)], check=True)
        # made again over a read-only in/
        F['prepare_share'](share, rel, commit, repo, None, dict(COMMIT=commit))
        self.assertFalse((share / 'in/candidate-keys.pub').exists())

    def test_a_private_key_is_refused(self):
        repo = self.root / 'repo'
        commit = make_repo(repo)
        with self.assertRaises(SystemExit):
            F['prepare_share'](self.root / 'share', release_dir(self.root / 'release'), commit, repo,
                               '-----BEGIN OPENSSH PRIVATE KEY-----\n', {})

    def test_an_unsigned_release_goes_without_signatures(self):
        # v0.0.1 ships unsigned (owner waiver): the share carries release.json and SHA256SUMS, and m27 in the guest decides
        repo = self.root / 'repo'
        commit = make_repo(repo)
        share = self.root / 'share'
        F['prepare_share'](share, release_dir(self.root / 'release', signed=False), commit, repo, None, {})
        self.assertEqual(sorted(p.name for p in (share / 'in/release').iterdir()), ['SHA256SUMS', 'release.json'])

    def test_a_release_without_sha256sums_is_refused(self):
        repo = self.root / 'repo'
        commit = make_repo(repo)
        rel = release_dir(self.root / 'release', signed=False)
        (rel / 'SHA256SUMS').unlink()
        with self.assertRaises(SystemExit):
            F['prepare_share'](self.root / 'share', rel, commit, repo, None, {})

    def test_reset_keeps_xcode_and_python(self):
        cmd = F['reset_command']('tester')
        home = '/'.join(('', 'Users', 'tester'))
        self.assertIn(home + '/hao-patcher', cmd)
        self.assertIn(home + '/make-work*', cmd)
        self.assertIn('launchctl bootout system/$l', cmd)
        self.assertNotIn('Xcode', cmd)
        self.assertNotIn('Python.framework', cmd)
        subprocess.run(['sh', '-n', '-c', cmd], check=True)
        with self.assertRaises(SystemExit):
            F['reset_command']('tester; rm -rf /')


class FakeVM:
    def __init__(self, share_out=None, **guest):
        self.calls, self.guest, self.up = [], guest, False

    def running(self):
        return self.up

    def start(self, share):
        self.calls.append(('start', str(share)))
        self.up, self.share = True, Path(share)
        return 0

    def exec(self, cmd, timeout=600):
        self.calls.append(('exec', cmd.split()[0]))
        if 'RESET-OK' in cmd:
            return 0, 'RESET-OK free_kb=1\n'
        if 'bootstrap' in cmd:
            guest_output(self.share / 'out', **self.guest)
            return 0, 'JOB-STARTED\n'
        return 0, 'JOB-OUT\n'

    def stop(self):
        self.calls.append(('stop',))
        self.up = False
        return 0


class Run(unittest.TestCase):
    """main() end to end: a real git commit (this checkout's HEAD), a fake mirror and a fake VM."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name).resolve()
        self.rel = release_dir(self.root / 'release')
        self.head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=TOP, capture_output=True,
                                   text=True).stdout.strip()
        self.lines = ['%s 5 https://example.invalid/dss_shim.dylib dss_shim.dylib' % ('1' * 64),
                      '%s 9 https://example.invalid/kdk.dmg KDK.dmg' % ('2' * 64)]
        self.patch = {}

    def tearDown(self):
        self.d.cleanup()

    def main(self, vm, have=None, *extra):
        have = set(have if have is not None else (l.split()[0] for l in self.lines))
        self.filled = []

        def fill(cmd, input=None, **kw):
            self.filled.append((cmd, input))
            have.update(l.split()[0] for l in input.splitlines())
        out = self.root / 'out'
        rc = F['main']([str(self.rel), self.head, '--out', str(out), '--vm', str(self.root / 'vm'), *extra],
                       vm=vm, store=self.root, check=lambda url, h: h in have, run=fill,
                       needs=lambda release: self.lines)
        return rc, json.loads((out / 'fresh-mac.json').read_text())

    def test_a_passing_run(self):
        vm = FakeVM(commit=self.head)
        rc, r = self.main(vm)
        self.assertEqual(rc, 0, r.get('reasons'))
        self.assertTrue(r['passed'])
        self.assertEqual(r['commit'], self.head)
        self.assertEqual(r['release_sha256'], F['sha256'](self.rel / 'release.json'))
        self.assertEqual(vm.calls[0][0], 'start')
        self.assertEqual(vm.calls[-1], ('stop',))
        self.assertFalse(vm.up)
        for key in ('seconds', 'stages', 'mirror_misses', 'stick_sha256', 'stick_allocated_bytes', 'logs'):
            self.assertIn(key, r)
        self.assertTrue(os.access(Path(r['share']) / 'in/params', os.W_OK))     # removable again

    def test_a_failing_guest_still_stops_the_vm(self):
        vm = FakeVM(commit=self.head, rc=1, stick=None)
        rc, r = self.main(vm)
        self.assertEqual(rc, 1)
        self.assertFalse(r['passed'])
        self.assertEqual(vm.calls[-1], ('stop',))

    def test_a_mirror_without_the_files_stops_before_the_vm(self):
        vm = FakeVM(commit=self.head)
        rc, r = self.main(vm, have=())
        self.assertEqual(rc, 1)
        self.assertEqual(vm.calls, [])
        self.assertEqual(len(r['mirror_missing']), 2)

    def test_missing_files_are_filled_before_the_vm_starts(self):
        vm = FakeVM(commit=self.head)
        rc, r = self.main(vm, set(), '--mirror-fill', '/bin/false')
        self.assertEqual(rc, 0, r.get('reasons'))
        self.assertEqual(r['mirror_filled'], ['dss_shim.dylib', 'KDK.dmg'])
        self.assertEqual(self.filled[0][0], ['/bin/false', 'fill', '-', '--from', str(self.rel)])
        self.assertEqual(vm.calls[0][0], 'start')

    def test_a_file_without_an_origin_is_not_filled(self):
        self.lines.append('%s 3 ? unknown.bin' % ('3' * 64))
        vm = FakeVM(commit=self.head)
        rc, r = self.main(vm, set(), '--mirror-fill', '/bin/false')
        self.assertEqual(rc, 1)
        self.assertNotIn('unknown.bin', self.filled[0][1])
        self.assertEqual([l.split()[-1] for l in r['mirror_missing']], ['unknown.bin'])
        self.assertEqual(vm.calls, [])

    def test_a_running_vm_is_left_alone(self):
        vm = FakeVM(commit=self.head)
        vm.up = True
        rc, r = self.main(vm)
        self.assertEqual(rc, 1)
        self.assertEqual(vm.calls, [])


if __name__ == '__main__':
    unittest.main()
