"""Partial reads are checked against pins: ranged.py (HTTP ranges, member pins), the package TOC (inplace.py) and
ipsw-stream (streamed and decrypted IPSW members), each failing closed on one flipped byte; and ipsw-stream makes the
same payload as patches/golden-gate-userspace/build/make from the same IPSW."""
import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile
import zlib

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / 'pipeline' / 'unpack'))
import ranged  # noqa: E402
import inplace  # noqa: E402

KEY = 'base64:' + base64.b64encode(bytes(range(32))).decode()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves server.files[path] with byte ranges; server.flip = (path, offset) flips one byte in what it sends."""
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.server.hits.append(self.path)
        data = self.server.files.get(self.path)
        if data is None:
            self.send_response(404); self.send_header('Content-Length', '0'); self.end_headers()
            return
        flip = self.server.flip
        if flip and flip[0] == self.path:
            data = bytearray(data); data[flip[1]] ^= 0x01; data = bytes(data)
        rng = self.headers.get('Range')
        if not rng or self.server.no_ranges:
            self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers()
            self.wfile.write(data)
            return
        a, b = rng.split('=')[1].split('-')
        a, b = int(a), min(int(b), len(data) - 1)
        pad = b'x' * (getattr(self.server, 'pad', 0) if rng != 'bytes=0-0' else 0)   # more than was asked for
        self.send_response(206)
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (a, b, len(data)))
        self.send_header('Content-Length', str(b - a + 1 + len(pad))); self.end_headers()
        self.wfile.write(data[a:b + 1] + pad)


class Served:
    def __init__(self, files):
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), RangeHandler)
        self.httpd.files, self.httpd.flip, self.httpd.no_ranges, self.httpd.hits = files, None, False, []
        self.httpd.handle_error = lambda *a: None      # a client that stops reading (a refused reply)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


def make_zip(members):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_STORED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return b.getvalue()


def pin(data):
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())


class Ranged(unittest.TestCase):
    def setUp(self):
        self.members = {'a.bin': os.urandom(3 << 20), 'b.txt': b'hello' * 1000}
        self.blob = make_zip(self.members)
        self.srv = Served({'/x.zip': self.blob})
        self.url = self.srv.base + '/x.zip'
        self.pins = ranged.Pins({n: pin(d) for n, d in self.members.items()}, 'member')

    def tearDown(self):
        self.srv.close()

    def zip(self, window=ranged.WINDOW):
        src = ranged.HTTPSource(self.url, window=window)
        return src, zipfile.ZipFile(src.reader())

    def test_pinned_members_read_by_range(self):
        src, z = self.zip(window=64 << 10)
        self.assertEqual(self.pins.read(z, 'a.bin'), self.members['a.bin'])
        self.assertEqual(self.pins.read(z, 'b.txt'), self.members['b.txt'])
        self.assertEqual(src.size, len(self.blob))

    def test_flipped_byte_in_a_ranged_read_is_refused(self):
        start = self.blob.index(self.members['a.bin'][:64])
        self.srv.httpd.flip = ('/x.zip', start + 12345)
        _, z = self.zip()
        with self.assertRaises(ranged.PinError):
            self.pins.read(z, 'a.bin')

    def test_member_not_pinned_is_not_read(self):
        _, z = self.zip()
        with self.assertRaises(ranged.PinError):
            ranged.Pins({'b.txt': pin(self.members['b.txt'])}, 'member').open(z, 'a.bin')

    def test_server_without_ranges_is_refused(self):
        self.srv.httpd.no_ranges = True
        with self.assertRaises(OSError):
            ranged.HTTPSource(self.url)

    def test_closing_before_the_end_is_refused(self):
        _, z = self.zip()
        s = ranged.Hashed(z.open('a.bin'), 'a.bin', pin(self.members['a.bin']), 'member')
        s.read(1000)
        with self.assertRaises(ranged.PinError) as e:
            s.close()
        self.assertIn('not checked', str(e.exception))
        raw = ranged.Hashed(z.open('b.txt'), 'b.txt', pin(self.members['b.txt']), 'member')
        self.assertEqual(raw.read(len(self.members['b.txt'])), self.members['b.txt'])
        raw.close()                                 # every pinned byte read: checked on close
        self.assertTrue(raw.checked)
        bad = dict(pin(self.members['b.txt']), sha256='0' * 64)
        raw = ranged.Hashed(z.open('b.txt'), 'b.txt', bad, 'member')
        raw.read(len(self.members['b.txt']))
        with self.assertRaises(ranged.PinError):
            raw.close()

    def test_a_reply_longer_than_asked_is_refused_unread(self):
        self.srv.httpd.pad = 1 << 20
        src = ranged.HTTPSource(self.url, window=64 << 10)
        with mock.patch.object(ranged.time, 'sleep', lambda s: None):
            with self.assertRaisesRegex(OSError, 'larger than the'):
                src.read_at(0, 100)

    def test_unpinned_reads_are_recorded(self):
        _, z = self.zip()
        p = ranged.Pins(None, 'member')
        p.read(z, 'b.txt')
        self.assertEqual(p.used, {'b.txt'})


class Mirror(unittest.TestCase):
    """M27_MIRROR: a URL with a known whole-file sha256 is read from MIRROR/sha256/HEX (Range, 206)."""

    def setUp(self):
        self.members = {'a.bin': os.urandom(1 << 20), 'b.txt': b'hello' * 1000}
        self.blob = make_zip(self.members)
        self.digest = hashlib.sha256(self.blob).hexdigest()
        self.srv = Served({'/origin/x.ipsw': self.blob, '/m/sha256/' + self.digest: self.blob})
        self.url = self.srv.base + '/origin/x.ipsw'
        self.pins = ranged.Pins({n: pin(d) for n, d in self.members.items()}, 'member')

    def tearDown(self):
        self.srv.close()

    def env(self, strict=False, **more):
        e = {k: v for k, v in os.environ.items() if not k.startswith('M27_MIRROR')}
        e.update(M27_MIRROR=self.srv.base + '/m/', **more)
        if strict:
            e['M27_MIRROR_STRICT'] = '1'
        return mock.patch.dict(os.environ, e, clear=True)

    def open(self, sha256=None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            src = ranged.open_source(self.url, sha256 or self.digest)
            data = self.pins.read(zipfile.ZipFile(src.reader()), 'a.bin')
        return src, data, err.getvalue()

    def test_range_reads_go_to_the_mirror(self):
        dead = 'http://127.0.0.1:9'                      # http.client: no proxy variable applies
        with self.env(http_proxy=dead, HTTP_PROXY=dead, all_proxy=dead):
            src, data, err = self.open()
        self.assertEqual((data, err), (self.members['a.bin'], ''))
        self.assertEqual(src.url, self.srv.base + '/m/sha256/' + self.digest)
        self.assertEqual({h for h in self.srv.httpd.hits}, {'/m/sha256/' + self.digest})

    def test_a_miss_reads_the_origin_after_one_line(self):
        del self.srv.httpd.files['/m/sha256/' + self.digest]
        with self.env():
            src, data, err = self.open()
        self.assertEqual((src.url, data), (self.url, self.members['a.bin']))
        self.assertEqual(err, 'mirror miss x.ipsw %s\n' % self.digest)

    def test_a_strict_miss_raises_naming_the_file(self):
        del self.srv.httpd.files['/m/sha256/' + self.digest]
        with self.env(strict=True):
            with self.assertRaisesRegex(ranged.MirrorMiss, 'x.ipsw \\(sha256 %s\\)' % self.digest):
                self.open()
        self.assertNotIn('/origin/x.ipsw', self.srv.httpd.hits)

    def test_wrong_bytes_on_the_mirror_are_refused_by_the_pins(self):
        self.srv.httpd.flip = ('/m/sha256/' + self.digest, self.blob.index(self.members['a.bin'][:64]) + 777)
        with self.env():
            with self.assertRaises(ranged.PinError):
                self.open()

    def test_mirrored_names_the_url_to_read(self):
        with self.env():
            self.assertEqual(ranged.mirrored(self.url, self.digest), self.srv.base + '/m/sha256/' + self.digest)
            self.assertEqual(ranged.mirrored('/a/local/file', self.digest), '/a/local/file')
        with mock.patch.dict(os.environ, {'M27_MIRROR': ''}):
            self.assertEqual(ranged.mirrored(self.url, self.digest), self.url)

    def test_the_mirrors_own_url_is_read_as_is_when_strict(self):
        with self.env(strict=True):
            url = ranged.mirrored(self.url, self.digest)
            src = ranged.open_source(url)              # a second opener without the sha256 (inplace.check_signature)
        self.assertEqual(src.url, self.srv.base + '/m/sha256/' + self.digest)
        self.assertNotIn('/origin/x.ipsw', self.srv.httpd.hits)

    def test_an_ipv6_mirror_is_refused(self):
        with mock.patch.dict(os.environ, {'M27_MIRROR': 'http://[fe80::1%25en0]:8080'}):
            with self.assertRaisesRegex(ranged.MirrorMiss, 'IPv4'):
                ranged.open_source(self.url, self.digest)


def make_xar(d, files):
    """A real xar package (/usr/bin/xar, stored uncompressed) of FILES {name: bytes}."""
    src = Path(d) / 'src'; src.mkdir()
    for n, data in files.items():
        (src / n).write_bytes(data)
    pkg = Path(d) / 'p.pkg'
    subprocess.run(['/usr/bin/xar', '-c', '--compression=none', '-f', str(pkg)] + list(files), cwd=src, check=True,
                   capture_output=True)
    return pkg


@unittest.skipUnless(Path('/usr/bin/xar').exists(), 'needs /usr/bin/xar')
class PackageTOC(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.pkg = make_xar(self.d.name, {'SharedSupport.dmg': os.urandom(100000), 'Bom': b'bom'})

    def tearDown(self):
        self.d.cleanup()

    def test_member_found_and_toc_pin_checked(self):
        src = ranged.open_source(self.pkg)
        _, _, comp = inplace.xar_toc(src)
        m = inplace.xar_member(src, toc_sha256=hashlib.sha256(comp).hexdigest())
        self.assertEqual(m['length'], 100000)
        with self.assertRaises(ranged.PinError):
            inplace.xar_member(src, toc_sha256='0' * 64)

    def test_flipped_byte_in_a_toc_entry_is_refused(self):
        data = bytearray(self.pkg.read_bytes())
        hsize = int.from_bytes(data[4:6], 'big'); tcomp = int.from_bytes(data[8:16], 'big')
        toc = zlib.decompress(bytes(data[hsize:hsize + tcomp]))
        # change one digit of an entry (the member's length) and recompress: the TOC still parses, its SHA-1 differs
        i = toc.index(b'<length>') + len(b'<length>')
        bad = toc[:i] + (b'2' if toc[i:i + 1] != b'2' else b'3') + toc[i + 1:]
        comp = zlib.compress(bad)
        data[8:16] = len(comp).to_bytes(8, 'big'); data[16:24] = len(bad).to_bytes(8, 'big')
        tampered = bytes(data[:hsize]) + comp + bytes(data[hsize + tcomp:])
        p = Path(self.d.name) / 'bad.pkg'; p.write_bytes(tampered)
        with self.assertRaises(ranged.PinError):
            inplace.xar_member(ranged.open_source(p))

    def test_unsigned_package_is_refused(self):
        with self.assertRaises(ranged.PinError):
            inplace.check_signature(self.pkg)


FAKE_IPSW = r'''#!/bin/sh
# a stand-in for blacktop ipsw: version, `fw aea -k FILE` (the test key) and `fw aea FILE --output DIR` (decrypt)
case "$1" in
version) echo "Version: 3.1.737, BuildCommit: test"; exit 0 ;;
fw) if [ "$3" = "-k" ]; then echo "%(key)s"; exit 0; fi
    name=$(basename "$3" .aea); exec /usr/bin/aea decrypt -key-value "%(key)s" -i "$3" -o "$5/$name" ;;
esac
exit 1
'''


@unittest.skipUnless(Path('/usr/bin/aea').exists(), 'needs /usr/bin/aea')
class IPSWStream(unittest.TestCase):
    """A small IPSW: a BuildManifest naming four images for board t1ap, two of them AEA-encrypted."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.root = Path(self.d.name)
        self.tool = self.root / 'ipsw'; self.tool.write_text(FAKE_IPSW % dict(key=KEY)); self.tool.chmod(0o755)
        self.images = {'fs.dmg': os.urandom(2 << 20), 'os.dmg': os.urandom(5 << 20),
                       'app.dmg': os.urandom(300000), 'ros.dmg': os.urandom(700000)}
        members = {}
        for name in ('fs.dmg', 'os.dmg'):
            plain = self.root / name; plain.write_bytes(self.images[name])
            subprocess.run(['/usr/bin/aea', 'encrypt', '-profile', 'hkdf_sha256_aesctr_hmac__symmetric__none',
                            '-key-value', KEY, '-i', str(plain), '-o', str(self.root / (name + '.aea'))], check=True)
            members[name + '.aea'] = (self.root / (name + '.aea')).read_bytes()
        members['app.dmg'], members['ros.dmg'] = self.images['app.dmg'], self.images['ros.dmg']
        identity = dict(Info=dict(DeviceClass='t1ap', Variant='Customer Erase Install (IPSW)'), Manifest={
            'OS': dict(Info=dict(Path='fs.dmg.aea')), 'Cryptex1,SystemOS': dict(Info=dict(Path='os.dmg.aea')),
            'Cryptex1,AppOS': dict(Info=dict(Path='app.dmg')), 'Cryptex1,RosettaOS': dict(Info=dict(Path='ros.dmg'))})
        members['BuildManifest.plist'] = plistlib.dumps(dict(ProductBuildVersion='1A1', ProductVersion='1.0',
                                                             BuildIdentities=[identity]))
        members['Firmware/other.bin'] = os.urandom(5000)
        self.members = members
        self.ipsw = self.root / 'test.ipsw'; self.ipsw.write_bytes(make_zip(members))
        roles = {'filesystem': 'fs.dmg', 'os': 'os.dmg', 'app': 'app.dmg', 'rosetta': 'ros.dmg'}
        self.pins = dict(members={n: pin(members[n]) for n in ('BuildManifest.plist', 'fs.dmg.aea', 'os.dmg.aea',
                                                              'app.dmg', 'ros.dmg')},
                         images={r: pin(self.images[f]) for r, f in roles.items()})
        self.pin_file = self.root / 'pins.json'; self.pin_file.write_text(json.dumps(self.pins))
        self.srv = Served({'/test.ipsw': self.ipsw.read_bytes()})

    def tearDown(self):
        self.srv.close(); self.d.cleanup()

    def stream(self, out, *extra, src=None, env=None):
        return subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/unpack/ipsw-stream'), '--ipsw',
                               src or self.srv.base + '/test.ipsw', '--board', 't1ap', '--out', str(out),
                               '--ipsw-tool', str(self.tool)] + list(extra), capture_output=True, text=True, env=env)

    def test_with_a_mirror_the_members_are_read_from_it(self):
        blob = self.ipsw.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        p = self.root / 'mirror-pins.json'; p.write_text(json.dumps(dict(self.pins, ipsw_sha256=digest)))
        self.srv.httpd.files = {'/m/sha256/' + digest: blob}          # the origin URL has nothing
        env = {k: v for k, v in os.environ.items() if not k.startswith('M27_MIRROR')}
        r = self.stream(self.root / 'mirrored', '--pins', str(p), env=dict(env, M27_MIRROR=self.srv.base + '/m'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(set(self.srv.httpd.hits), {'/m/sha256/' + digest})
        self.assertEqual((self.root / 'mirrored/payload/rosetta.dmg').read_bytes(), self.images['ros.dmg'])
        self.srv.httpd.files = {'/test.ipsw': blob}                    # strict and a mirror without it: no origin
        self.srv.httpd.hits.clear()
        r = self.stream(self.root / 'strict', '--pins', str(p),
                        env=dict(env, M27_MIRROR=self.srv.base + '/m', M27_MIRROR_STRICT='1'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('test.ipsw (sha256 %s)' % digest, r.stderr)
        self.assertEqual(self.srv.httpd.hits, ['/m/sha256/' + digest])

    def test_streamed_payload_equals_the_userspace_make(self):
        r = self.stream(self.root / 's', '--pins', str(self.pin_file))
        self.assertEqual(r.returncode, 0, r.stderr)
        m = subprocess.run([sys.executable, '-I', str(TOP / 'patches/golden-gate-userspace/build/make'), '--ipsw',
                            str(self.ipsw), '--board', 't1ap', '--out', str(self.root / 'm'), '--ipsw-tool',
                            str(self.tool)], capture_output=True, text=True)
        self.assertEqual(m.returncode, 0, m.stderr)
        made = sorted(p.name for p in (self.root / 'm/payload').iterdir() if p.name != 'userspace.json')
        streamed = sorted(p.name for p in (self.root / 's/payload').iterdir() if p.name != 'userspace.json')
        self.assertEqual(made, streamed)
        self.assertEqual(made, ['app.dmg', 'filesystem.dmg', 'identity.plist', 'os.dmg', 'rosetta.dmg'])
        for name in made:
            self.assertEqual((self.root / 'm/payload' / name).read_bytes(), (self.root / 's/payload' / name).read_bytes(),
                             name)
        self.assertFalse(list((self.root / 's').rglob('*.aea')))
        self.assertFalse(list((self.root / 's').glob('.spool-*')))

    def assert_refused(self, r, out):
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list((out / 'payload').iterdir()), [], 'nothing unverified is left')
        self.assertEqual(json.loads((out / 'build.json').read_text())['status'], 'failed')

    def test_flipped_byte_in_a_streamed_member_is_refused(self):
        for member in ('ros.dmg', 'os.dmg.aea'):
            with self.subTest(member=member):
                blob = self.ipsw.read_bytes()
                self.srv.httpd.flip = ('/test.ipsw', blob.index(self.members[member][:64]) + 100000)
                out = self.root / ('flip-' + member)
                self.assert_refused(self.stream(out, '--pins', str(self.pin_file)), out)

    def test_wrong_image_pin_is_refused(self):
        bad = json.loads(self.pin_file.read_text()); bad['images']['os']['sha256'] = '0' * 64
        p = self.root / 'bad.json'; p.write_text(json.dumps(bad))
        out = self.root / 'badimg'
        self.assert_refused(self.stream(out, '--pins', str(p)), out)

    def test_unpinned_member_is_refused(self):
        bad = json.loads(self.pin_file.read_text()); del bad['members']['app.dmg']
        p = self.root / 'unpinned.json'; p.write_text(json.dumps(bad))
        out = self.root / 'unpinned'
        self.assert_refused(self.stream(out, '--pins', str(p)), out)

    def test_aea_member_reaches_no_tool_before_its_pin_is_checked(self):
        log = self.root / 'tool.log'
        logging = self.root / 'ipsw-logging'
        logging.write_text('#!/bin/sh\necho "$@" >> %s\nexec %s "$@"\n' % (log, self.tool)); logging.chmod(0o755)
        blob = self.ipsw.read_bytes()
        self.srv.httpd.flip = ('/test.ipsw', blob.index(self.members['os.dmg.aea'][:64]) + 10)   # in the key prologue
        out = self.root / 'flip-prologue'
        r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline/unpack/ipsw-stream'), '--ipsw',
                            self.srv.base + '/test.ipsw', '--board', 't1ap', '--out', str(out), '--ipsw-tool',
                            str(logging), '--pins', str(self.pin_file)], capture_output=True, text=True)
        self.assert_refused(r, out)
        calls = log.read_text()
        self.assertIn('filesystem.prefix.aea', calls)          # the checked member before it was read
        self.assertNotIn('os.prefix.aea', calls)
        self.assertEqual([p.name for p in out.iterdir() if p.name.startswith('.spool-')], [])

    def test_url_needs_pins(self):
        r = self.stream(self.root / 'nopins', '--trust-local')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('only with --pins', r.stderr)


if __name__ == '__main__':
    unittest.main()
