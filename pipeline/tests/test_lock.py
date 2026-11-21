"""Behaviour of pipeline/lock.py (inputs locks, target files) and pipeline/run's arguments. python3 -m unittest discover -s pipeline/tests"""
import hashlib, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
LOCK = [sys.executable, '-I', str(TOP / 'pipeline/lock.py')]


def lock(*args):
    return subprocess.run(LOCK + [str(a) for a in args], capture_output=True, text=True)


class Lock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.f = self.d / 'payload'; self.f.write_bytes(b'abc')
        self.sha = hashlib.sha256(b'abc').hexdigest()

    def write(self, text):
        p = self.d / 'x.lock'; p.write_text(text); return p

    def test_rows_comments_and_hash(self):
        p = self.write('# header\nkdk %s %s   # trailing note\ntree - ~/somewhere\n' % (self.sha, self.f))
        self.assertEqual(lock('path', p, 'kdk').stdout.strip(), str(self.f))
        self.assertEqual(lock('path', p, 'tree').stdout.strip(), os.path.expanduser('~/somewhere'))

    def test_relative_path_is_from_the_repository(self):
        p = self.write('tree - store/inputs/tree\n')
        self.assertEqual(lock('path', p, 'tree').stdout.strip(), str(TOP / 'store/inputs/tree'))
        t = self.d / 't.json'
        t.write_text(json.dumps({'esp': {'path': 'store/esp.tar', 'sha256': '-'}}))
        self.assertEqual(lock('path', t, 'esp').stdout.strip(), str(TOP / 'store/esp.tar'))

    def test_wrong_hash_or_missing_row_stops(self):
        p = self.write('kdk %s %s\n' % ('0' * 64, self.f))
        r = lock('path', p, 'kdk'); self.assertNotEqual(r.returncode, 0); self.assertIn('is not', r.stderr)
        r = lock('path', p, 'nope'); self.assertNotEqual(r.returncode, 0); self.assertIn('no row nope', r.stderr)

    def test_take_copies_only_the_rows_bytes(self):
        p = self.write('kdk %s %s\ntree - %s\n' % (self.sha, self.f, self.f))
        out = self.d / 'out'; out.mkdir()
        r = lock('take', p, 'kdk', out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(Path(r.stdout.strip()).read_bytes(), b'abc')
        other = self.d / 'other'; other.write_bytes(b'abd')
        for args in (('kdk', out / 'o1', other), ('tree', out / 'o2')):
            (out / args[1].name).mkdir()
            r = lock('take', p, *args)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(list((out / args[1].name).iterdir()), [], 'nothing is left on a refusal')
        r = lock('take', p, 'kdk', out / 'o1', self.f)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_take_never_removes_a_file_it_did_not_create(self):
        p = self.write('kdk %s %s\n' % (self.sha, self.f))
        out = self.d / 'out'; out.mkdir()
        (out / 'payload').write_bytes(b'already here')
        r = lock('take', p, 'kdk', out)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((out / 'payload').read_bytes(), b'already here')
        r = lock('take', p, 'kdk', self.d)          # the source's own folder as the destination
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.f.read_bytes(), b'abc')

    def tree_row(self):
        t = self.d / 'tree'; (t / 'sub').mkdir(parents=True)
        (t / 'sub' / 'a').write_bytes(b'a'); (t / 'sub' / 'a').chmod(0o755)
        (t / 'sub').chmod(0o750)
        (t / 'ln').symlink_to('sub/a')
        (t / 'notes').write_bytes(b'not hashed')
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock as L
        want = L.row_sha256(str(t), without=('notes',))
        d = self.d / 'treelock'; d.mkdir()
        (d / 'site').write_text('# hash: without notes\n%s %s\n' % (want, t))
        return t, d, want, L

    def test_take_tree_copies_and_checks_the_tree(self):
        t, d, want, L = self.tree_row()
        out = self.d / 'copy'
        r = lock('take-tree', d, 'site', out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(L.row_sha256(str(out), without=('notes',)), want)
        self.assertEqual(((out / 'sub').stat().st_mode & 0o777, (out / 'sub' / 'a').stat().st_mode & 0o777), (0o750, 0o755))
        self.assertEqual(os.readlink(out / 'ln'), 'sub/a')
        self.assertFalse((out / 'notes').exists(), 'what the row does not hash is not copied')
        (t / 'sub' / 'a').write_bytes(b'evil')
        self.assertEqual((out / 'sub' / 'a').read_bytes(), b'a')

    def test_take_tree_refuses_a_changed_tree_and_an_existing_destination(self):
        t, d, want, L = self.tree_row()
        (t / 'sub' / 'a').write_bytes(b'evil')
        r = lock('take-tree', d, 'site', self.d / 'copy')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('(site)', r.stderr)
        self.assertFalse((self.d / 'copy').exists())
        (t / 'sub' / 'a').write_bytes(b'a')
        there = self.d / 'there'; there.mkdir(); (there / 'keep').write_bytes(b'k')
        r = lock('take-tree', d, 'site', there)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((there / 'keep').read_bytes(), b'k', 'a destination this call did not make is left alone')

    def test_stage_copies_a_row_and_checks_the_copy(self):
        t, d, want, L = self.tree_row()
        os.utime(t / 'sub' / 'a', ns=(10**18, 10**18))
        (d / 'kdk').write_text('%s %s\n' % (self.sha, self.f))
        out = self.d / 'staged'
        r = lock('stage', d, 'site', out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(L.row_sha256(str(out), without=('notes',)), want)
        self.assertEqual((out / 'sub' / 'a').stat().st_mtime_ns, 10**18, 'metadata kept, as cp -Rp')
        self.assertTrue((out / 'notes').exists(), 'every entry is copied, as before')
        (t / 'sub' / 'a').write_bytes(b'evil')
        self.assertEqual((out / 'sub' / 'a').read_bytes(), b'a')
        r = lock('stage', d, 'kdk', self.d / 'kdk-copy')
        self.assertEqual((r.returncode, (self.d / 'kdk-copy').read_bytes()), (0, b'abc'))
        self.assertEqual(lock('where', d, 'kdk').stdout.strip(), str(self.f))

    def test_stage_refuses_changed_bytes_existing_destinations_and_unpinned_rows(self):
        t, d, want, L = self.tree_row()
        (t / 'sub' / 'a').write_bytes(b'evil')
        r = lock('stage', d, 'site', self.d / 'staged')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.d / 'staged').exists(), 'a copy that does not check is removed')
        (d / 'kdk').write_text('%s %s\n' % ('0' * 64, self.f))
        r = lock('stage', d, 'kdk', self.d / 'kdk-copy')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.d / 'kdk-copy').exists())
        there = self.d / 'there'; there.write_bytes(b'keep')
        (d / 'kdk').write_text('%s %s\n' % (self.sha, self.f))
        self.assertNotEqual(lock('stage', d, 'kdk', there).returncode, 0)
        self.assertEqual(there.read_bytes(), b'keep', 'a destination this call did not make is left alone')
        (d / 'loose').write_text('- %s\n' % self.f)
        r = lock('stage', d, 'loose', self.d / 'loose-copy')
        self.assertIn('no sha256', r.stderr)
        (d / 'tool').write_text('- pipeline/lock.py\n')     # a '-' row of this checkout: pinned by the commit
        self.assertEqual(lock('stage', d, 'tool', self.d / 'tool-copy').returncode, 0)

    @staticmethod
    def acl(p):
        out = subprocess.run(['/bin/ls', '-lde', str(p)], capture_output=True, text=True).stdout
        return [l.strip() for l in out.splitlines()[1:] if l.strip()[:1].isdigit()]

    def grant(self, p):
        """An ACL entry that lets everyone write P: the source's, which no checked copy may keep."""
        subprocess.run(['/bin/chmod', '+a', 'everyone allow write,append,delete,writeattr,writeextattr', str(p)],
                       check=True)
        self.assertTrue(self.acl(p))

    def test_no_copy_keeps_the_sources_acl_or_set_id_bits(self):
        t, d, want, L = self.tree_row()
        self.grant(t / 'sub' / 'a'); self.grant(t / 'sub'); self.grant(t)
        self.grant(self.f); self.f.chmod(0o4777)
        (d / 'kdk').write_text('%s %s\n' % (self.sha, self.f))
        out = self.d / 'out'; out.mkdir()
        for args, made in ((('stage', d, 'site', self.d / 'staged'), self.d / 'staged'),
                           (('stage', d, 'kdk', self.d / 'kdk-copy'), self.d / 'kdk-copy'),
                           (('take-tree', d, 'site', self.d / 'taken'), self.d / 'taken'),
                           (('take', d, 'kdk', out), out / 'payload')):
            with self.subTest(how=args[0]):
                r = lock(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                for p in [made] + (list(made.rglob('*')) if made.is_dir() else []):
                    if not p.is_symlink():
                        self.assertEqual(self.acl(p), [], p)
                        self.assertEqual(p.lstat().st_uid, os.geteuid(), p)
                if made.is_file():
                    self.assertEqual(made.stat().st_mode & 0o7022, 0, 'no set-id bit, no one else may write it')
        self.assertEqual([p.name for p in self.d.rglob('.stage-*')], [], 'no staging folder is left')

    def test_acls_are_cleared_in_a_tree_with_framework_links(self):
        # OCLP's partial frameworks: links to Versions/Current/... with no Current folder, so they dangle; chmod -R -N
        # fails on a dangling link (the OCLP hardware row, candidate 02b692df's create-installer)
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock as L
        fw = self.d / 'tree' / 'Lib' / 'X.framework'
        (fw / 'Versions' / 'A' / 'Resources').mkdir(parents=True)
        (fw / 'Versions' / 'A' / 'X').write_bytes(b'x')
        (fw / 'Resources').symlink_to('Versions/Current/Resources')
        (fw / 'X').symlink_to('Versions/Current/X')
        self.grant(fw / 'Versions' / 'A' / 'X'); self.grant(fw)
        L._no_acl(str(self.d / 'tree'), tree=True)
        for p in [self.d / 'tree'] + list((self.d / 'tree').rglob('*')):
            if not p.is_symlink():
                self.assertEqual(self.acl(p), [], p)

    def test_the_staging_folder_is_private(self):
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock as L
        parent = self.d / 'parent'; parent.mkdir()
        subprocess.run(['/bin/chmod', '+a', 'everyone allow add_file,add_subdirectory,file_inherit,directory_inherit',
                        str(parent)], check=True)
        p = Path(L.private_dir(str(parent)))
        self.assertEqual((p.stat().st_mode & 0o777, self.acl(p)), (0o700, []))

    def test_a_staging_folder_reading_as_uid_99_needs_a_volume_without_owners(self):
        # a volume mounted without ownership (a USB installer drive) reports every owner as uid 99; a folder there
        # is accepted, and uid 99 elsewhere is not
        sys.path.insert(0, str(TOP / 'pipeline'))
        import lock as L
        from unittest import mock
        real = os.lstat
        def as_99(p, *a, **k):
            st = real(p, *a, **k)
            if '.stage-' not in str(p):
                return st
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, L.UNKNOWN_UID) + tuple(st)[5:10])
        parent = self.d / 'drive'; parent.mkdir()
        with mock.patch.object(L.os, 'lstat', as_99):
            with mock.patch.object(L, 'ignores_owners', return_value=True):
                self.assertTrue(Path(L.private_dir(str(parent))).is_dir())
            with mock.patch.object(L, 'ignores_owners', return_value=False), self.assertRaises(SystemExit):
                L.private_dir(str(parent))

    def taken(self, p, name, stage=False):
        """lock.py take (a file row) or stage (STAGE: any row) into a new folder: where the source is checked."""
        out = Path(tempfile.mkdtemp(dir=self.d))
        return lock('stage', p, name, out / 'x') if stage else lock('take', p, name, out)

    def test_every_name_on_the_way_must_be_root_or_ours(self):
        import runpy
        from unittest import mock
        L = runpy.run_path(str(TOP / 'pipeline/lock.py'), run_name='lock_test')
        g = L['source_check'].__globals__
        safe = self.d / 'safe'; safe.mkdir(); (safe / 'payload').write_bytes(b'abc')
        middle = self.d / 'middle'; middle.mkdir(); os.symlink(safe, middle / 'current')
        link = self.d / 'link'; os.symlink(middle / 'current' / 'payload', link)
        L['source_check']('x.lock', 'kdk', str(link))           # all ours: no stop
        # as another uid sees them, every name on the way (the link, the hop between, the file) is someone else's
        with mock.patch.object(g['os'], 'geteuid', lambda: os.getuid() + 1), \
                mock.patch.dict(g['os'].environ, {'SUDO_UID': ''}):
            with self.assertRaises(SystemExit) as x:
                L['source_check']('x.lock', 'kdk', str(link))
        self.assertIn('belongs to another user', str(x.exception))
        r = self.taken(self.write('kdk %s %s\n' % (self.sha, link)), 'kdk'); self.assertEqual(r.returncode, 0, r.stderr)

    def test_dot_dot_after_a_link_is_taken_as_the_kernel_takes_it(self):
        import runpy
        L = runpy.run_path(str(TOP / 'pipeline/lock.py'), run_name='lock_test')
        (self.d / 'a' / 'b').mkdir(parents=True); (self.d / 'c').mkdir()
        os.symlink(self.d / 'a' / 'b', self.d / 'c' / 'jump')
        names = L['names_on_the_way'](str(self.d / 'c' / 'jump' / '..' / 'x'))
        real = Path(os.path.realpath(self.d))
        self.assertIn(str(real / 'a' / 'b'), names)               # followed through the link, then '..' to a/
        self.assertEqual(names[-1], str(real / 'a' / 'x'))          # not c/x, which folding '..' first would give

    def test_lock_folder_one_file_per_row(self):
        d = self.d / 'lockdir'; d.mkdir()
        (d / 'kdk').write_text('# a comment\n%s %s   # note\n' % (self.sha, self.f))
        self.assertEqual(lock('path', d, 'kdk').stdout.strip(), str(self.f))
        (d / 'bad').write_text('only-one-field\n')
        self.assertNotEqual(lock('path', d, 'kdk').returncode, 0)

    def payload(self, **over):
        d = self.d / 'pl'; d.mkdir(exist_ok=True)
        (d / 'libm27.dylib').write_bytes(b'm27')
        m = {'version': '26B131-abc', 'commit': 'a' * 40, 'clean': True, 'os_build': '26B131', 'cache_uuid': 'U',
             'units_version': 'u', 'units_sha256': 'b' * 64, 'layout_version': 1,
             'files': {'libm27.dylib': hashlib.sha256(b'm27').hexdigest()}}
        m.update(over)
        (d / 'payload-manifest.json').write_text(json.dumps(m))
        return d

    def row(self, text):
        d = self.d / 'lockdir'; d.mkdir(exist_ok=True)
        (d / 'm27_runtime').write_text(text)
        return d

    def test_payload_row_checked_by_its_manifest(self):
        pl = self.payload()
        msha = hashlib.sha256((pl / 'payload-manifest.json').read_bytes()).hexdigest()
        d = self.row('# check: payload os_build=26B131 layout_version=1 manifest_sha256=%s\n- %s\n' % (msha, pl))
        r = lock('path', d, 'm27_runtime'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(pl))
        for text, why in (('# check: payload os_build=26A428 layout_version=1\n', 'OS build'),
                          ('# check: payload os_build=26B131 layout_version=2\n', 'install-layout'),
                          ('# check: payload os_build=26B131 layout_version=1 manifest_sha256=%s\n' % ('0' * 64), 'is not'),
                          ('# check: payload layout_version=1\n', 'needs os_build'),
                          ('# check: nonsense\n', 'unknown check'),
                          ('# check: payload os_build\n', 'bad check line')):
            r = lock('path', self.row(text + '- %s\n' % pl), 'm27_runtime')
            self.assertNotEqual(r.returncode, 0, text); self.assertIn(why, r.stderr)
        (pl / 'libm27.dylib').write_bytes(b'changed')
        r = lock('path', self.row('# check: payload os_build=26B131 layout_version=1\n- %s\n' % pl), 'm27_runtime')
        self.assertNotEqual(r.returncode, 0); self.assertIn('does not match', r.stderr)

    def test_payload_row_listed_file_under_without_may_be_absent(self):
        pl = self.payload(files={'libm27.dylib': hashlib.sha256(b'm27').hexdigest(),
                                 'later/x.bin': hashlib.sha256(b'x').hexdigest()})
        check = '# check: payload os_build=26B131 layout_version=1\n'
        r = lock('path', self.row(check + '- %s\n' % pl), 'm27_runtime')
        self.assertNotEqual(r.returncode, 0); self.assertIn('later/x.bin is listed but missing', r.stderr)
        d = self.row(check + '# hash: without later\n- %s\n' % pl)
        r = lock('path', d, 'm27_runtime'); self.assertEqual(r.returncode, 0, r.stderr)
        (pl / 'later').mkdir(); (pl / 'later' / 'x.bin').write_bytes(b'wrong')
        r = lock('path', d, 'm27_runtime')
        self.assertNotEqual(r.returncode, 0); self.assertIn('later/x.bin does not match', r.stderr)

    def test_payload_row_dirty_or_unchecked(self):
        pl = self.payload(clean=False)
        r = lock('path', self.row('# check: payload os_build=26B131 layout_version=1\n- %s\n' % pl), 'm27_runtime')
        self.assertNotEqual(r.returncode, 0); self.assertIn('dirty', r.stderr)
        r = lock('path', self.row('# a plain comment mentioning check\n- %s\n' % pl), 'm27_runtime')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_duplicate_row_stops(self):
        r = lock('path', self.write('kdk - /a\nkdk - /b\n'), 'kdk')
        self.assertNotEqual(r.returncode, 0); self.assertIn('twice', r.stderr)

    def test_bad_row_stops(self):
        r = lock('path', self.write('kdk onlytwo\n'), 'kdk')
        self.assertNotEqual(r.returncode, 0); self.assertIn('bad row', r.stderr)

    def test_target_file(self):
        t = self.d / 't.json'
        t.write_text(json.dumps({'board_id': 'Mac-X', 'esp': {'path': str(self.f), 'sha256': self.sha},
                                 'oclp_hardware_profile': {'model': 'M'}}))
        self.assertEqual(lock('path', t, 'esp').stdout.strip(), str(self.f))
        self.assertEqual(lock('target', t, 'board_id').stdout.strip(), 'Mac-X')
        self.assertEqual(json.loads(lock('target', t, 'oclp_hardware_profile').stdout), {'model': 'M'})

    def test_kdk_name_and_psp(self):
        self.assertEqual(lock('kdk-name', '/k/Kernel_Debug_Kit_26.7.2_build_25G330.dmg').stdout.strip(), 'KDK_26.7.2_25G330.kdk')
        self.assertNotEqual(lock('kdk-name', '/k/other.dmg').returncode, 0)
        c = self.d / 'oclp/opencore_legacy_patcher'; c.mkdir(parents=True)
        (c / 'constants.py').write_text('        self.patcher_support_pkg_version:     str = "2.0.2"  # PSP\n')
        self.assertEqual(lock('psp', self.d / 'oclp').stdout.strip(), '2.0.2')


    def tree(self):
        t = self.d / 'tree'; (t / 'sub').mkdir(parents=True)
        (t / 'a').write_bytes(b'abc'); (t / 'sub' / 'b').write_bytes(b'def'); os.symlink('a', t / 'ln')
        return t

    def test_folder_row_is_checked_by_content(self):
        t = self.tree()
        h = lock('hash', t).stdout.strip()
        self.assertRegex(h, '^[0-9a-f]{64}$')
        p = self.write('tree %s %s\n' % (h, t))
        self.assertEqual(lock('path', p, 'tree').stdout.strip(), str(t))
        for change in (lambda: (t / 'sub' / 'b').write_bytes(b'xyz'),     # content
                       lambda: os.chmod(t / 'a', 0o755),                  # mode
                       lambda: (t / 'sub' / 'new').write_bytes(b''),      # added file
                       lambda: os.rename(t / 'ln', t / 'ln2')):           # name
            change()
            r = lock('path', p, 'tree'); self.assertNotEqual(r.returncode, 0, change); self.assertIn('is not', r.stderr)
            h2 = lock('hash', t).stdout.strip(); self.assertNotEqual(h2, h)
            p = self.write('tree %s %s\n' % (h2, t)); h = h2

    def test_hashed_row_that_is_missing_stops(self):
        r = lock('path', self.write('tree %s %s\n' % ('0' * 64, self.d / 'gone')), 'tree')
        self.assertNotEqual(r.returncode, 0); self.assertIn('is not there', r.stderr)

    def test_seal_writes_the_hash_and_keeps_comments(self):
        t = self.tree(); d = self.d / 'lockdir'; d.mkdir()
        (d / 'tree').write_text('# made by x\n- %s   # note\n' % t)
        r = lock('seal', d, 'tree'); self.assertEqual(r.returncode, 0, r.stderr)
        h = lock('hash', t).stdout.strip()
        self.assertEqual((d / 'tree').read_text(), '# made by x\n%s %s   # note\n' % (h, t))
        self.assertEqual(lock('path', d, 'tree').stdout.strip(), str(t))
        r = lock('seal', d, 'tree'); self.assertNotEqual(r.returncode, 0); self.assertIn('already', r.stderr)

    def test_store_rows_carry_a_hash(self):
        """A row under store/ names what it holds by sha256 (a folder's content_sha256), so a build record and a
        release say what was built and every read checks it; '-' is only for a file of this checkout (the commit pins
        it) or a row whose artifact is not made yet (its path does not exist, so no build can read it)."""
        L = __import__('runpy').run_path(str(TOP / 'pipeline/lock.py'))
        bad = []
        for d, _, files in os.walk(TOP / 'pipeline/inputs'):
            if files:
                for name, (sha, p) in L['rows'](d).items():
                    if sha == '-' and not L['in_checkout'](p) and os.path.exists(p):
                        bad.append('%s/%s' % (os.path.relpath(d, TOP), name))
        self.assertEqual(bad, [], 'seal with: python3.14 -I pipeline/lock.py seal LOCK NAME')


class LocalPins(unittest.TestCase):
    """$MACOS27_LOCAL_PINS replaces a row's sha256 for an artifact this Mac built itself; the row file is untouched."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.f = self.d / 'payload'; self.f.write_bytes(b'built here')
        self.sha = hashlib.sha256(b'built here').hexdigest()
        self.row = self.d / 'x.lock'
        self.row.write_text('shim %s %s\ntree - %s\n' % ('0' * 64, self.f, self.f))
        self.pins = self.d / 'pins.json'
        self.key = os.path.relpath(self.row, TOP) + '/shim'

    def run_lock(self, *args, pins=True):
        env = dict(os.environ)
        env.pop('MACOS27_LOCAL_PINS', None)
        if pins:
            env['MACOS27_LOCAL_PINS'] = str(self.pins)
        return subprocess.run(LOCK + [str(a) for a in args], capture_output=True, text=True, env=env)

    def test_without_pins_the_tracked_hash_stands(self):
        self.pins.write_text(json.dumps({self.key: self.sha}))
        self.assertNotEqual(self.run_lock('path', self.row, 'shim', pins=False).returncode, 0)

    def test_a_pin_replaces_the_tracked_hash_and_the_row_file_is_unchanged(self):
        before = self.row.read_text()
        self.pins.write_text(json.dumps({self.key: self.sha}))
        r = self.run_lock('path', self.row, 'shim')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(self.f)), r.stderr)
        self.assertEqual(self.row.read_text(), before)

    def test_a_pin_for_a_changed_artifact_still_fails(self):
        self.pins.write_text(json.dumps({self.key: self.sha}))
        self.f.write_bytes(b'changed')
        self.assertNotEqual(self.run_lock('path', self.row, 'shim').returncode, 0)

    def test_pin_local_records_what_the_row_holds(self):
        r = self.run_lock('pin-local', self.row, 'shim')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.pins.read_text()), {self.key: self.sha})
        self.assertEqual(self.run_lock('path', self.row, 'shim').returncode, 0)
        self.assertNotEqual(self.run_lock('pin-local', self.row, 'shim', pins=False).returncode, 0)

    def test_a_malformed_pin_is_refused(self):
        self.pins.write_text(json.dumps({self.key: 'abc'}))
        r = self.run_lock('path', self.row, 'shim')
        self.assertNotEqual(r.returncode, 0); self.assertIn('not a sha256', r.stderr)
        self.pins.write_text(json.dumps({self.key: int('1' * 64)}))
        r = self.run_lock('path', self.row, 'shim')
        self.assertNotEqual(r.returncode, 0); self.assertIn('not a sha256', r.stderr)
        self.pins.write_text('[]')
        r = self.run_lock('path', self.row, 'shim')
        self.assertNotEqual(r.returncode, 0); self.assertIn('JSON object', r.stderr)

    def test_pin_local_refuses_unknown_or_unchecked_rows(self):
        r = self.run_lock('pin-local', self.row, 'no_such_row')
        self.assertNotEqual(r.returncode, 0); self.assertIn('has no row', r.stderr)
        r = self.run_lock('pin-local', self.row, 'tree')
        self.assertNotEqual(r.returncode, 0); self.assertIn('unchecked', r.stderr)

    def test_pin_local_also_pins_payload_manifest_sha256(self):
        pl = self.d / 'pl'; pl.mkdir()
        (pl / 'libm27.dylib').write_bytes(b'm27')
        m = {'version': '26B131-abc', 'commit': 'a' * 40, 'clean': True, 'os_build': '26B131', 'cache_uuid': 'U',
             'units_version': 'u', 'units_sha256': 'b' * 64, 'layout_version': 1,
             'files': {'libm27.dylib': hashlib.sha256(b'm27').hexdigest()}}
        (pl / 'payload-manifest.json').write_text(json.dumps(m))
        lockdir = self.d / 'lockdir'; lockdir.mkdir()
        (lockdir / 'm27_runtime').write_text(
            '# check: payload os_build=26B131 layout_version=1 manifest_sha256=%s\n%s %s\n' % ('0' * 64, '1' * 64, pl))
        self.assertNotEqual(self.run_lock('path', lockdir, 'm27_runtime').returncode, 0)
        r = self.run_lock('pin-local', lockdir, 'm27_runtime')
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_lock('path', lockdir, 'm27_runtime')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(pl)), r.stderr)
        (pl / 'libm27.dylib').write_bytes(b'tampered')
        self.assertNotEqual(self.run_lock('path', lockdir, 'm27_runtime').returncode, 0)


class Key(unittest.TestCase):
    """lock.py key: cache keys of what the inputs are, not of where the store is or of row comments."""
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def store(self, where, payload=b'abc', folder='artifacts/thing-1234'):
        st = self.d / where / 'store'
        (st / folder).mkdir(parents=True); (st / folder / 'f').write_bytes(b'x')
        (st / 'k.bin').write_bytes(payload)
        return st

    def lockdir(self, name, rows):
        d = self.d / name; d.mkdir(parents=True, exist_ok=True)
        for n, text in rows.items():
            (d / n).write_text(text)
        return d

    def rows_for(self, st, comment='# note\n', sha=None):
        return {'kdk': '%s%s %s\n' % (comment, sha or hashlib.sha256(b'abc').hexdigest(), st / 'k.bin'),
                'tree': '%s- %s\n' % (comment, st / 'artifacts/thing-1234')}

    def key(self, st, locks, *items):
        r = lock('key', st, ':'.join(map(str, locks)), *items)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_store_move_and_comments_keep_the_key(self):
        a = self.store('old'); b = self.store('new')
        la = self.lockdir('a', self.rows_for(a)); lb = self.lockdir('b', self.rows_for(b, comment='# another note\n'))
        ka = self.key(a, [la], 'row:kdk', 'row:tree', 'text:x')
        self.assertRegex(ka, '^[0-9a-f]{12}$')
        self.assertEqual(ka, self.key(b, [lb], 'row:kdk', 'row:tree', 'text:x'))

    def test_content_changes_miss(self):
        st = self.store('s'); base = self.key(st, [self.lockdir('a', self.rows_for(st))], 'row:kdk', 'row:tree')
        other = self.lockdir('b', self.rows_for(st, sha=hashlib.sha256(b'abd').hexdigest()))
        self.assertNotEqual(base, self.key(st, [other], 'row:kdk', 'row:tree'))   # a new hash in the row
        (st / 'artifacts/thing-5678').mkdir()
        moved = self.lockdir('c', dict(self.rows_for(st), tree='- %s\n' % (st / 'artifacts/thing-5678')))
        self.assertNotEqual(base, self.key(st, [moved], 'row:kdk', 'row:tree'))   # a '-' row naming another artifact

    def test_dash_file_row_counts_by_content(self):
        st = self.store('s'); f = st / 'artifacts/one'; f.write_bytes(b'1')
        lk = self.lockdir('a', {'one': '- %s\n' % f})
        k1 = self.key(st, [lk], 'row:one'); f.write_bytes(b'2')
        self.assertNotEqual(k1, self.key(st, [lk], 'row:one'))

    def test_lock_order_and_missing_row(self):
        st = self.store('s')
        build = self.lockdir('build', {'kdk': '%s %s\n' % ('1' * 64, st / 'k.bin')})
        common = self.lockdir('common', self.rows_for(st))
        lines = lock('key-lines', st, '%s:%s' % (build, common), 'row:kdk', 'row:tree').stdout.splitlines()
        self.assertEqual(lines, ['row kdk ' + '1' * 64, 'row tree store/artifacts/thing-1234'])
        r = lock('key', st, str(common), 'row:nope')
        self.assertNotEqual(r.returncode, 0); self.assertIn('no row nope', r.stderr)
        r = lock('key', st, str(common), 'bogus:1')
        self.assertNotEqual(r.returncode, 0); self.assertIn('bad item', r.stderr)

    def test_target_json_counts_hashes_not_paths(self):
        st = self.store('s'); t = self.d / 'mbp.json'
        def write(path, sha='a' * 64, note='n', model='M'):
            t.write_text(json.dumps({'model': model, 'esp': {'path': path, 'sha256': sha, 'note': note}}, indent=1))
            return self.key(st, [], 'json:%s' % t)
        k = write('~/old/store/esp.tar')
        self.assertEqual(k, write('~/new/store/esp.tar', note='other note'))
        self.assertNotEqual(k, write('~/new/store/esp.tar', sha='b' * 64))
        self.assertNotEqual(k, write('~/new/store/esp.tar', model='N'))

    def test_patch_counts_revision_and_src(self):
        st = self.store('s')
        def patch(top, rev=1, body='echo hi\n'):
            d = self.d / top / 'patches/demo'; (d / 'src').mkdir(parents=True, exist_ok=True)
            (d / 'patch.json').write_text(json.dumps({'revision': rev, 'stage': 'oclp-hook'}))
            (d / 'src/hook.sh').write_text(body)
            return self.key(st, [], 'patch:%s' % d)
        k = patch('one')
        self.assertEqual(k, patch('two'))   # another checkout
        self.assertNotEqual(k, patch('one', rev=2))
        self.assertNotEqual(k, patch('one', body='echo bye\n'))

    def test_file_and_text_items(self):
        st = self.store('s'); f = self.d / 'stage.sh'; f.write_text('a')
        k = self.key(st, [], 'file:%s' % f, 'text:fd83 one\ntwo')
        self.assertEqual(k, self.key(st, [], 'file:%s' % f, 'text:fd83  one two'))
        f.write_text('b'); self.assertNotEqual(k, self.key(st, [], 'file:%s' % f, 'text:fd83 one two'))


class Repo(unittest.TestCase):
    def test_shipped_locks_and_targets_parse(self):
        inputs = TOP / 'pipeline/inputs'
        locks = [d for d in inputs.iterdir() if d.name != 'golden'] + sorted((inputs / 'golden').glob('*'))
        for d in locks:
            for f in d.iterdir():
                data = [l.split('#', 1)[0].split() for l in f.read_text().splitlines() if not l.lstrip().startswith('#')]
                self.assertEqual([len(x) for x in data if x], [2], '%s/%s' % (d.name, f.name))
        for t in (TOP / 'pipeline/targets').glob('*.json'):
            j = json.loads(t.read_text())
            # the generic target (software graphics) has no board id and no OCLP hardware; its ESP and OpenCore
            # configuration come with its model pack
            want = {'model', 'facts'} if j.get('generic') else {'model', 'board_id', 'esp', 'oclp_hardware_profile'}
            self.assertTrue(want <= set(j), t.name)

    def test_run_needs_four_arguments(self):
        r = subprocess.run(['zsh', str(TOP / 'pipeline/run'), 'mbp92', '25G332'], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('OCLP', r.stderr)


if __name__ == '__main__':
    unittest.main()


class ReleaseFilesScope(unittest.TestCase):
    """`# hash: release-files`: a folder row's sha256 covers only what a release can give a user's Mac (its
    release-classes.json include plus made_on_mac_files), so a store made from a release matches the same row."""

    def setUp(self):
        import runpy
        self.L = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'lock.py'))
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.full, self.user = d / 'dev', d / 'user'
        for top, extra in ((self.full, True), (self.user, False)):
            (top / 'runtime').mkdir(parents=True)
            (top / 'build.json').write_text('{}')
            (top / 'runtime' / 'Lib.dylib').write_bytes(b'ours')
            (top / 'runtime' / 'detector').write_bytes(b'made on the Mac')
            if extra:                                     # in the dev store only: never published
                (top / 'runtime' / 'notes.txt').write_text('dev')
                (top / 'logs').mkdir()
                (top / 'logs' / 'build.log').write_text('x')
        self.only = ['build.json', 'runtime/Lib.dylib', 'runtime/detector']

    def tearDown(self):
        self.tmp.cleanup()

    def test_scope_ignores_what_a_release_cannot_carry(self):
        self.assertNotEqual(self.L['content_sha256'](str(self.full)), self.L['content_sha256'](str(self.user)))
        self.assertEqual(self.L['content_sha256'](str(self.full), self.only),
                         self.L['content_sha256'](str(self.user), self.only))
        (self.user / 'runtime' / 'detector').write_bytes(b'other')        # a covered file differs: the hash differs
        self.assertNotEqual(self.L['content_sha256'](str(self.full), self.only),
                            self.L['content_sha256'](str(self.user), self.only))

    def test_rows_that_declare_it_name_release_classes_lists(self):
        top = Path(__file__).resolve().parents[2]
        classes = json.loads((top / 'pipeline' / 'release-classes.json').read_text())['rows']
        found = 0
        for f in sorted((top / 'pipeline' / 'inputs').rglob('*')):
            if f.is_file() and any(self.L['HASH_SCOPE'].match(l) for l in f.read_text().splitlines()):
                found += 1
                only = self.L['scope'](str(f.parent), f.name)
                entry = classes[f.name]
                self.assertEqual(only, entry['include'] + [m['path'] for m in entry.get('made_on_mac_files', [])])
        self.assertEqual(found, 8)


class HashWithout(unittest.TestCase):
    """`# hash: without NAME...`: a folder row's sha256 leaves out its producer's record, so a remake matches."""

    def test_record_left_out_payload_counted(self):
        import runpy
        L = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'lock.py'))
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / 'a', Path(t) / 'b'
            for top, date in ((a, '2026-11-09'), (b, '2026-11-14')):
                (top / 'payload' / 'x').mkdir(parents=True)
                (top / 'payload' / 'x' / 'f').write_bytes(b'same')
                (top / 'build.json').write_text('{"date": "%s", "path": "/somewhere/%s"}' % (date, date))
                (top / 'build').mkdir()
                (top / 'build' / 'tmp.ll').write_text(date)
            self.assertNotEqual(L['content_sha256'](str(a)), L['content_sha256'](str(b)))
            self.assertEqual(L['content_sha256'](str(a), None, ('build.json', 'build')),
                             L['content_sha256'](str(b), None, ('build.json', 'build')))
            (b / 'payload' / 'x' / 'f').write_bytes(b'other')
            self.assertNotEqual(L['content_sha256'](str(a), None, ('build.json', 'build')),
                                L['content_sha256'](str(b), None, ('build.json', 'build')))
            lock = Path(t) / 'lock'
            lock.mkdir()
            (lock / 'r').write_text('# hash: without build.json build  # records\n- %s\n' % a)
            self.assertEqual(L['without'](str(lock), 'r'), ('build.json', 'build'))


class VerifyStore(unittest.TestCase):
    """lock.py verify-store: a pinned store row that changed is named with its newest files; unchanged rows are not
    rehashed again (fingerprint state)."""

    def test_changed_folder_row_is_named(self):
        import json, os, runpy, tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            store = d / 'store'; (store / 'art' / 'site').mkdir(parents=True)
            (store / 'art' / 'site' / 'm.py').write_text('x = 1\n')
            locks = d / 'inputs' / 'common'; locks.mkdir(parents=True)
            L = runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))
            g = L['verify_store'].__globals__
            saved = dict(TOP=g['TOP'])
            g['TOP'] = str(d)
            try:
                h = L['row_sha256'](str(store / 'art' / 'site'))
                (locks / 'site_row').write_text('%s store/art/site\n' % h)
                g['store_locks'] = lambda inputs=None: [str(locks)]
                state = str(d / 'state.json')
                self.assertIn('1 rehashed, 0 unchanged, 0 changed', L['verify_store']('--state', state))
                self.assertIn('0 rehashed, 1 unchanged', L['verify_store']('--state', state))
                (store / 'art' / 'site' / '__pycache__').mkdir()
                (store / 'art' / 'site' / '__pycache__' / 'm.cpython-312.pyc').write_bytes(b'pyc')
                with self.assertRaises(SystemExit) as e:
                    L['verify_store']('--state', state)
                self.assertIn('common/site_row', str(e.exception))
                self.assertIn('__pycache__', str(e.exception))
            finally:
                g.update(saved)
