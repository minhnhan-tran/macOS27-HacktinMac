"""pipeline/candidate-cut with fake steps: the release stick and the dev stick, and create-installer's phase times."""
from pathlib import Path
import hashlib
import json
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

TOP = Path(__file__).resolve().parents[2]
C = runpy.run_path(str(TOP / 'pipeline' / 'candidate-cut'))


class Cut(unittest.TestCase):
    def test_unpublished_create_installer_fetches_staged_cut(self):
        # Real installer orchestration, URL preflight and acquisition; mocked
        # host checks, kit preparation and disks. Hosted origins do not exist.
        fixtures = runpy.run_path(str(TOP/'tools/macOS27Tool/tests/test_installer.py'))
        inst = fixtures['INST']
        acquire = runpy.run_path(str(TOP/'tools/acquire/acquire'))
        real = self.fake_run
        checked = []
        def run(cmd, cwd, log, env):
            cmd = [str(c) for c in cmd]
            if 'pipeline/release-cut' in cmd:
                rc = real(cmd, cwd, log, env)
                path = Path(cmd[cmd.index('--write')+1])
                rel = json.loads(path.read_text())
                rel.update(assembly='mbp92-26B131', proven_models=['MacBookPro9,2'], downloads=[], assets=[])
                for name in ('python_arm64', 'python_x86_64', 'kit', 'metal_llvm', 'runtime'):
                    data = ('pinned '+name).encode()
                    (path.parent/name).write_bytes(data)
                    row = dict(row=name, url='https://github.com/minhnhan-tran/macOS27-HacktinMac/releases/download/unpublished/'+name,
                               size=len(data), sha256=hashlib.sha256(data).hexdigest())
                    rel['downloads' if name.startswith('python') else 'assets'].append(
                        dict(row, **({'name':name} if name.startswith('python') else {'file':name})))
                path.write_text(json.dumps(rel))
                return rc
            if 'create-installer' in cmd:
                path = Path(cmd[cmd.index('--release')+1])
                cache = Path(cmd[cmd.index('--cache')+1])
                self.assertEqual(env['M27_BOOTSTRAP_FILES'], str(path.parent))
                def survey(purpose, where, release, cache, **kw):
                    return inst.DOCTOR.check_urls(release, {}, False,
                        lambda *a, **k: self.fail('candidate HEAD reached public origin'))
                c, _, _ = fixtures['console']([])
                args = fixtures['ns'](release=str(path), cache=str(cache), model='MacBookPro9,2',
                                      image=cmd[cmd.index('--image')+1], image_size=64*10**9, dry_run=False)
                with mock.patch.dict('os.environ', env), mock.patch.object(inst, 'DOCTOR_CHECKS', survey), \
                     fixtures['FakeWriter'](), mock.patch.object(inst, 'finish_kit', return_value=[]):
                    self.assertEqual(inst.create_installer(args, c, fixtures['FakeMac'](), acquire=acquire), 0)
                    for row in acquire['load'](path):
                        self.assertTrue(acquire['intact'](cache, row))
                        checked.append(row['name'])
                return real(cmd, cwd, log, env)
            return real(cmd, cwd, log, env)
        self.fake_run = run
        rc, _, _ = self.cut('--no-dev')
        self.assertEqual(rc, 0)
        self.assertEqual(len(checked), 5)

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name).resolve()
        (self.root / 'store').mkdir()
        self.calls = []

    def tearDown(self):
        self.d.cleanup()

    def fake_checkout(self, dev=True):
        def make(sha, tree):
            (tree / 'pipeline/assemblies').mkdir(parents=True)
            (tree / 'tools/macOS27Tool/data').mkdir(parents=True)
            (tree / 'pipeline/release-cut').write_text("--candidate\n" + ("'--dev'\n" if dev else ''))
            for name in ('mbp92-26B131-alpha-v0.1.0', 'dev-26B131-alpha-v0.1.0'):
                (tree / 'pipeline/assemblies' / (name + '.json')).write_text('{}')
        return make, lambda tree: None

    def fake_run(self, cmd, cwd, log, env):
        cmd = [str(c) for c in cmd]
        self.calls.append((cmd, env['M27_LOGS']))
        log.write_text('ran\n')
        if 'pipeline/release-cut' in cmd:
            out = Path(cmd[cmd.index('--write') + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({'candidate': True, **({'dev': True} if '--dev' in cmd else {}),
                                       'assembly': Path(cmd[cmd.index('--write') + 2]).stem}))
        if 'create-installer' in cmd:
            Path(cmd[cmd.index('--image') + 1]).write_bytes(b'stick for ' + cmd[cmd.index('--release') + 1].encode())
        return 0

    def cut(self, *extra, dev=True):
        out = self.root / 'out'
        rc = C['main'](['HEAD', '--out', str(out)] + list(extra), store=self.root / 'store', run=self.fake_run,
                       worktree=self.fake_checkout(dev))
        return rc, json.loads((out / 'candidate.json').read_text()), out

    def test_both_sticks_are_made_and_listed(self):
        rc, rec, out = self.cut()
        self.assertEqual(rc, 0)
        self.assertTrue(rec['passed'])
        self.assertEqual([s['kind'] for s in rec['sticks']], ['release', 'dev'])
        rel, dev = rec['sticks']
        self.assertEqual(rel['path'], str(out / 'stick.img'))
        self.assertEqual(dev['path'], str(out / 'dev' / 'stick.img'))
        for s in rec['sticks']:
            self.assertEqual(s['sha256'], C['sha256'](s['path']))
            self.assertEqual(s['size'], Path(s['path']).stat().st_size)
        self.assertTrue((out / 'dev' / 'stick.img.sha256').read_text().startswith(dev['sha256']))
        self.assertEqual(rec['dev']['image_sha256'], dev['sha256'])
        self.assertTrue(rec['dev']['passed'])
        cuts = [c for c, _ in self.calls if 'pipeline/release-cut' in c]
        self.assertEqual(len(cuts), 2)
        self.assertIn('--dev', cuts[1])
        self.assertIn('pipeline/assemblies/dev-26B131-alpha-v0.1.0.json', cuts[1])
        self.assertTrue(json.loads((out / 'dev/release/release.json').read_text())['dev'])
        self.assertEqual({e for c, e in self.calls if 'create-installer' in c},
                         {str(out / 'logs'), str(out / 'dev' / 'logs')})

    def test_the_release_stick_is_unchanged_by_the_dev_stick(self):
        rc, with_dev, out = self.cut()
        release_calls = [c for c, logs in self.calls if logs == str(out / 'logs')]
        self.calls = []
        shutil.rmtree(out)
        rc, without, out = self.cut('--no-dev')
        self.assertEqual(without['dev'], {'skipped': '--no-dev'})
        self.assertEqual([s['kind'] for s in without['sticks']], ['release'])
        self.assertEqual(without['sticks'][0], with_dev['sticks'][0])
        self.assertEqual([c for c, _ in self.calls], release_calls)
        self.assertNotIn('--dev', [x for c, _ in self.calls for x in c])
        self.assertFalse('dev' in json.loads((out / 'release/release.json').read_text()))

    def test_a_commit_without_the_dev_cut_is_recorded_as_skipped(self):
        rc, rec, out = self.cut(dev=False)
        self.assertEqual(rc, 0)
        self.assertIn('--dev', rec['dev']['skipped'])
        self.assertEqual(len(rec['sticks']), 1)

    def test_a_failed_dev_step_fails_the_cut(self):
        real = self.fake_run

        def run(cmd, cwd, log, env):
            return 1 if 'create-installer' in [str(c) for c in cmd] and '/dev/' in str(log) else real(cmd, cwd, log, env)
        self.fake_run = run
        rc, rec, out = self.cut()
        self.assertEqual(rc, 1)
        self.assertFalse(rec['passed'])
        self.assertFalse(rec['dev']['passed'])
        self.assertEqual(rec['dev']['steps'][-1]['name'], 'create-installer-dev')


    def test_only_dev_makes_only_the_dev_stick(self):
        rc, rec, out = self.cut('--only-dev')
        self.assertEqual(rc, 0)
        self.assertEqual([s['kind'] for s in rec['sticks']], ['dev'])
        self.assertEqual(rec['release_stick'], {'skipped': '--only-dev'})
        self.assertFalse((out / 'stick.img').exists())
        self.assertTrue(all('--dev' in c for c, _ in self.calls if 'pipeline/release-cut' in c))

    def test_generic_cuts_one_release_and_a_second_stick(self):
        rc, rec, out = self.cut('--no-dev', '--generic')
        self.assertEqual(rc, 0)
        self.assertIsNone(rec['fresh_mac'])
        self.assertEqual([s['name'] for s in rec['sticks']], ['release', 'generic'])
        cut = next(c for c, _ in self.calls if 'pipeline/release-cut' in c)
        self.assertIn('pipeline/assemblies/mbp92-26B131-alpha-v0.1.0.json', cut)
        self.assertIn('pipeline/assemblies/generic-26B131.json', cut)
        gen = [c for c, _ in self.calls if 'create-installer' in c][-1]
        self.assertEqual(gen[gen.index('--profile') + 1], 'generic')
        self.assertEqual(gen[gen.index('--image') + 1], str(out / 'generic' / 'stick.img'))
        self.assertEqual(gen[gen.index('--release') + 1], str(out / 'release' / 'release.json'))
        self.assertTrue((out / 'generic/stick.img.sha256').read_text().startswith(rec['sticks'][1]['sha256'] + '  stick.img'))
        self.assertTrue((out / 'stick.img.sha256').read_text().endswith('  stick.img\n'))

    def test_a_cut_that_cannot_start_still_writes_candidate_json(self):
        def broken(sha, tree):
            raise OSError('no worktree')
        out = self.root / 'out'
        rc = C['main'](['HEAD', '--out', str(out)], store=self.root / 'store', run=self.fake_run,
                       worktree=(broken, lambda tree: None))
        rec = json.loads((out / 'candidate.json').read_text())
        self.assertEqual(rc, 1)
        self.assertFalse(rec['passed'])
        self.assertIsNone(rec['fresh_mac'])

    def fresh(self, passed, rc=None):
        real = self.fake_run

        def run(cmd, cwd, log, env):
            cmd = [str(c) for c in cmd]
            if 'pipeline/fresh-mac-run' not in cmd:
                return real(cmd, cwd, log, env)
            self.calls.append((cmd, env['M27_LOGS']))
            log.write_text('ran\n')
            where = Path(cmd[cmd.index('--out') + 1])
            where.mkdir(parents=True)
            (where / 'fresh-mac.json').write_text(json.dumps(dict(passed=passed, stick_sha256='ab' * 32)))
            return (0 if passed else 1) if rc is None else rc
        self.fake_run = run
        return self.cut('--no-dev', '--fresh-mac', '--fresh-mac-vm', str(self.root / 'vm'))

    def test_fresh_mac_runs_last_on_the_release_stick(self):
        rc, rec, out = self.fresh(True)
        self.assertEqual(rc, 0)
        self.assertEqual(rec['fresh_mac'], 'PASS')
        self.assertTrue(rec['fresh_mac_result']['passed'])
        self.assertEqual(rec['steps'][-1]['name'], 'fresh-mac')
        cmd = self.calls[-1][0]
        self.assertEqual(cmd[cmd.index('pipeline/fresh-mac-run') + 1], str(out / 'release'))
        self.assertEqual(cmd[cmd.index('--mirror') + 1], 'http://192.168.64.1:8765')
        self.assertEqual(cmd[cmd.index('--vm') + 1], str(self.root / 'vm'))

    def test_a_fresh_mac_failure_fails_the_cut(self):
        rc, rec, out = self.fresh(False)
        self.assertEqual(rc, 1)
        self.assertFalse(rec['passed'])
        self.assertEqual(rec['fresh_mac'], 'FAIL')

    def test_fresh_mac_without_a_result_is_a_failure(self):
        real = self.fake_run
        self.fake_run = lambda cmd, cwd, log, env: 3 if 'pipeline/fresh-mac-run' in [str(c) for c in cmd] \
            else real(cmd, cwd, log, env)
        rc, rec, out = self.cut('--no-dev', '--fresh-mac', '--fresh-mac-vm', str(self.root / 'vm'))
        self.assertEqual(rc, 1)
        self.assertEqual(rec['fresh_mac'], 'FAIL')
        self.assertIsNone(rec['fresh_mac_result'])


def sh(cwd, *cmd):
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=True).stdout


class Overlay(unittest.TestCase):
    """--dev-overlay REV: the dev cut files REV changed since its merge base with COMMIT, applied in the cut's
    worktree of COMMIT and committed there; COMMIT's own later changes to the same files are kept."""

    def test_the_dev_cut_is_applied_on_top_of_the_commit(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d).resolve() / 'repo'
            (repo / 'pipeline/profiles').mkdir(parents=True)
            sh(Path(d), 'git', 'init', '-q', '-b', 'main', str(repo))
            for k, v in (('user.name', 'test'), ('user.email', 'test@localhost'), ('core.hooksPath', '/dev/null')):
                sh(repo, 'git', 'config', k, v)
            classes = ['{', ' "rows": {', '  "a": 1,'] + ['  "pad%d": %d,' % (i, i) for i in range(8)] + \
                      ['  "z": 2', ' }', '}']
            (repo / 'pipeline/release-classes.json').write_text('\n'.join(classes) + '\n')
            (repo / 'pipeline/release-cut').write_text('cut\n')
            (repo / 'pipeline/patches.py').write_text('import sys\nprint("x\\ndev-access")\n')
            sh(repo, 'git', 'add', '-A')
            sh(repo, 'git', 'commit', '-q', '-m', 'base')
            sh(repo, 'git', 'checkout', '-q', '-b', 'dev')
            dev_classes = classes[:2] + ['  "test_host_key": 3,'] + classes[2:]
            (repo / 'pipeline/release-classes.json').write_text('\n'.join(dev_classes) + '\n')
            (repo / 'pipeline/release-cut').write_text("cut\n'--dev'\n")
            for f in C['DEV_FILES'][3:]:
                (repo / f).parent.mkdir(parents=True, exist_ok=True)
                (repo / f).write_text('{}\n' if f.endswith('.json') else 'x\n')
            (repo / 'unrelated.txt').write_text('not part of the dev cut\n')
            sh(repo, 'git', 'add', '-A')
            sh(repo, 'git', 'commit', '-q', '-m', 'dev cut')
            sh(repo, 'git', 'checkout', '-q', 'main')
            later = classes[:-3] + ['  "z": 20', ' }', '}']          # the commit's own later change, kept
            (repo / 'pipeline/release-classes.json').write_text('\n'.join(later) + '\n')
            sh(repo, 'git', 'commit', '-q', '-am', 'later')
            sha = sh(repo, 'git', 'rev-parse', 'HEAD').strip()
            tree = Path(d).resolve() / 'tree'
            sh(repo, 'git', 'worktree', 'add', '-q', '--detach', str(tree), sha)
            got = C['dev_overlay'](tree, sha, 'dev', repo=repo)
            self.assertEqual(got['base_commit'], sha)
            self.assertTrue(got['set_regenerated'])
            self.assertEqual((tree / 'pipeline/sets/dev-alpha-v0.1.0.txt').read_text(), 'x\ndev-access\n')
            text = (tree / 'pipeline/release-classes.json').read_text()
            self.assertIn('"test_host_key": 3', text)
            self.assertIn('"z": 20', text)
            self.assertIn("'--dev'", (tree / 'pipeline/release-cut').read_text())
            self.assertFalse((tree / 'unrelated.txt').exists())
            self.assertEqual(sh(tree, 'git', 'status', '--porcelain', '--untracked-files=no'), '')
            self.assertEqual(sh(tree, 'git', 'rev-parse', 'HEAD^').strip(), sha)
            self.assertEqual(got['cut_commit'], sh(tree, 'git', 'rev-parse', 'HEAD').strip())


class Phases(unittest.TestCase):
    def test_phases_from_the_event_log(self):
        events = [dict(ev='run.start', t=3.0), dict(ev='step.start', t=10.0, step='Download'),
                  dict(ev='command', t=12.0, argv=['py', '-I', '/x/pipeline/place']),
                  dict(ev='command', t=14.0, argv=['py', '-I', '/x/pipeline/make-on-mac']),
                  dict(ev='task.start', t=50.0, text='  stage   inputs'), dict(ev='task.end', t=90.0),
                  dict(ev='run.end', t=95.0)]
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(C['phases'](d))
            (Path(d) / 'create-installer-1.json').write_text(json.dumps({'events': events}))
            self.assertEqual(C['phases'](d), [['doctor and questions', 10.0], ['step: Download', 2.0],
                                              ['place', 2.0], ['make-on-mac', 36.0], ['stage inputs', 40.0]])


if __name__ == '__main__':
    unittest.main()
