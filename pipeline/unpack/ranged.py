"""ranged.py: read a large file by byte ranges, from disk or over HTTP(S), and check what is read against pins.

A release pins the bytes the build uses, not whole downloads: each zip member it reads (an IPSW's disk images, the
installer's asset zip members) has its size and sha256 in release.json, and a member is hashed while it streams and
refused at its end if it differs. Nothing read from a source counts until its pin is checked.

  open_source(PATH_OR_URL, sha256=None, name=None)
                             -> Source: .size, .read_at(off, n), .reader() (seekable file object), .fetched
  mirrored(URL, sha256, name=None) -> the URL to read: the local mirror's when it serves the file, else URL
  Pins(DICT, what)            {member: {"size": N, "sha256": HEX}}: .open(zipfile, member) -> a stream that raises
                              PinError at its end when the bytes differ (and at once when the member is not pinned),
                              and when it is closed before all of it was read

  ranged.py pins ZIP MEMBER...   the pins of MEMBERs of a trusted local zip (an IPSW), as JSON

A local mirror: with M27_MIRROR=URL set, a URL whose whole-file SHA256 is known is read from URL/sha256/SHA256 instead
(the mirror answers Range with 206); the member pins check what is read exactly as before. A mirror miss (HTTP error,
no connection, no byte ranges) prints one line, `mirror miss NAME SHA256` (stderr; NAME is the URL's file name), and
the origin URL is read; with M27_MIRROR_STRICT=1 it raises MirrorMiss (an OSError) naming the file and its sha256.
M27_MIRROR is an IPv4 (or host name) URL, e.g. http://192.168.64.1:PORT; an IPv6 address is refused. Connections are
http.client's own, so no proxy variable applies (to the mirror or the origin).
"""
import hashlib
import http.client
import io
import os
import re
import sys
import time
import urllib.parse
import zipfile

WINDOW = 16 << 20          # a remote read fetches at least this much (sequential zip reads are many small reads)
RETRIES = 5


class PinError(Exception):
    """Bytes that are not the pinned ones: never used."""


class MirrorMiss(OSError):
    """M27_MIRROR_STRICT=1 and the local mirror does not serve the file."""


class Source:
    fetched = 0

    def reader(self, window=WINDOW):
        return io.BufferedReader(_Raw(self), window)


class FileSource(Source):
    def __init__(self, path):
        self.path = str(path)
        self.f = open(self.path, 'rb')
        self.size = os.fstat(self.f.fileno()).st_size

    def read_at(self, off, n):
        self.f.seek(off)
        b = self.f.read(n)
        self.fetched += len(b)
        return b


class HTTPSource(Source):
    """A URL read with Range requests over one kept-alive connection; a short or wrong reply is retried, then
    refused. A redirect is followed once per connection (Apple's CDNs and GitHub's release downloads redirect)."""

    def __init__(self, url, window=WINDOW):
        self.url, self.window, self.conn = url, window, None
        self.cache_off, self.cache = 0, b''
        try:
            self.size = self._size()
        except BaseException:
            if self.conn:
                self.conn.close()
            raise

    def _connect(self, url):
        u = urllib.parse.urlsplit(url)
        cls = http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection
        self.conn, self.host, self.target = cls(u.netloc, timeout=60), u.netloc, (u.path or '/') + (
            '?' + u.query if u.query else '')

    @staticmethod
    def _body(r, most):
        """At most MOST bytes of response R's body; OSError when the server sends more (never read past MOST+1)."""
        body = r.read(most + 1)
        if len(body) > most:
            raise OSError('reply larger than the %d bytes asked for' % most)
        return body

    def _request(self, start, end):
        url = self.url
        for _ in range(5):
            if self.conn is None:
                self._connect(url)
            self.conn.request('GET', self.target, headers={'Range': 'bytes=%d-%d' % (start, end),
                                                           'User-Agent': 'm27-ranged/1'})
            r = self.conn.getresponse()
            if r.status in (301, 302, 303, 307, 308):
                self.conn.close(); self.conn = None          # the redirect's body is not read
                url = urllib.parse.urljoin(url, r.getheader('Location'))
                continue
            return r
        raise OSError('%s: too many redirects' % self.url)

    def _size(self):
        r = self._request(0, 0)
        self._body(r, 1 << 16)                  # one byte asked for; an error page may be longer, never unbounded
        rng = r.getheader('Content-Range') or ''
        if r.status != 206 or not rng.startswith('bytes 0-0/'):
            raise OSError('%s does not serve byte ranges (HTTP %d)' % (self.url, r.status))
        return int(rng.split('/')[1])

    def _get(self, off, n):
        end = min(off + n, self.size) - 1
        err = None
        for attempt in range(RETRIES):
            try:
                r = self._request(off, end)
                body = self._body(r, end - off + 1)
                if r.status != 206 or not (r.getheader('Content-Range') or '').startswith('bytes %d-%d/' % (off, end)):
                    raise OSError('HTTP %d, Content-Range %r for bytes %d-%d' % (r.status, r.getheader('Content-Range'),
                                                                               off, end))
                if len(body) != end - off + 1:
                    raise OSError('short reply: %d of %d bytes' % (len(body), end - off + 1))
                self.fetched += len(body)
                return body
            except (OSError, http.client.HTTPException) as x:
                err = x
                if self.conn:
                    self.conn.close()
                self.conn = None
                time.sleep(min(2 ** attempt, 20))
        raise OSError('%s: bytes %d-%d: %s' % (self.url, off, end, err))

    def read_at(self, off, n):
        if off >= self.size or n <= 0:
            return b''
        n = min(n, self.size - off)
        c0, c1 = self.cache_off, self.cache_off + len(self.cache)
        if c0 <= off and off + n <= c1:
            return self.cache[off - c0:off - c0 + n]
        if n >= self.window:
            return self._get(off, n)
        self.cache_off, self.cache = off, self._get(off, self.window)
        return self.cache[:n]


def _from_mirror(url, sha256, name):
    """An HTTPSource of URL's file on the M27_MIRROR mirror (by SHA256), or None (not set, or a miss: one line)."""
    base = os.environ.get('M27_MIRROR', '').strip()
    if not base:
        return None
    if url.startswith(base.rstrip('/') + '/sha256/'):  # already the mirror's URL (from mirrored()): read it as is
        return None
    strict = os.environ.get('M27_MIRROR_STRICT', '') == '1'
    name = name or urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1]) or url
    if not (isinstance(sha256, str) and re.fullmatch(r'[0-9a-f]{64}', sha256)):
        if strict:
            raise MirrorMiss('%s: no whole-file sha256 to ask the mirror for; M27_MIRROR_STRICT=1, the origin is not '
                             'read' % name)
        print('mirror miss %s -' % name, file=sys.stderr, flush=True)
        return None
    murl = base.rstrip('/') + '/sha256/' + sha256
    netloc = urllib.parse.urlsplit(murl).netloc
    if not murl.startswith(('http://', 'https://')):
        raise MirrorMiss('M27_MIRROR %r is not an http(s) URL' % base)
    if '[' in netloc or '%' in netloc:          # _connect builds the connection from netloc: no IPv6 zone address
        raise MirrorMiss('M27_MIRROR %r: an IPv6 address is not supported; use an IPv4 URL such as '
                         'http://192.168.64.1:PORT' % base)
    try:
        return HTTPSource(murl)
    except (OSError, http.client.HTTPException) as x:
        if strict:
            raise MirrorMiss('%s (sha256 %s): not served by the mirror %s: %s; M27_MIRROR_STRICT=1, the origin is not '
                             'read' % (name, sha256, murl, x)) from None
        print('mirror miss %s %s' % (name, sha256), file=sys.stderr, flush=True)
        return None


def open_source(where, sha256=None, name=None):
    """A Source of a path or URL; a URL is read from the M27_MIRROR mirror when it has the file (by SHA256)."""
    w = str(where)
    if not w.startswith(('http://', 'https://')):
        return FileSource(w)
    return _from_mirror(w, sha256, name) or HTTPSource(w)


def mirrored(url, sha256, name=None):
    """The URL to read URL's file from (for a program that opens it more than once): the mirror's, when M27_MIRROR is
    set and the mirror serves SHA256, else URL itself (after one `mirror miss` line, or MirrorMiss when strict)."""
    u = str(url)
    if not u.startswith(('http://', 'https://')):
        return u
    src = _from_mirror(u, sha256, name)
    if src is None:
        return u
    if src.conn:
        src.conn.close()
    return src.url


class _Raw(io.RawIOBase):
    def __init__(self, src):
        self.src, self.pos = src, 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.src.size + off
        return self.pos

    def readinto(self, b):
        data = self.src.read_at(self.pos, len(b))
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


class Hashed(io.RawIOBase):
    """STREAM read through sha256; at its end the size and digest must equal the pin, else PinError. Closing it before
    its end is a PinError too: nothing read from it was checked."""

    def __init__(self, stream, name, pin, what):
        self.s, self.name, self.pin, self.what = stream, name, pin, what
        self.h, self.n, self.checked, self.error = hashlib.sha256(), 0, False, None

    def readable(self): return True

    def readinto(self, b):
        try:
            data = self.s.read(len(b))
        except zipfile.BadZipFile as x:          # the zip's own CRC-32 saw it first: the same refusal
            raise PinError('%s %s: %s' % (self.what, self.name, x)) from None
        if data:
            self.h.update(data); self.n += len(data)
            if self.n > self.pin['size']:
                self.error = PinError('%s %s: more than the pinned %d bytes' % (self.what, self.name, self.pin['size']))
                raise self.error
        else:
            self.check()
        b[:len(data)] = data
        return len(data)

    def check(self):
        if self.checked:
            return
        if self.n != self.pin['size'] or self.h.hexdigest() != self.pin['sha256']:
            self.error = PinError('%s %s: got %d bytes, sha256 %s; pinned %d, %s' % (
                self.what, self.name, self.n, self.h.hexdigest(), self.pin['size'], self.pin['sha256']))
            raise self.error
        self.checked = True

    def close(self):
        if self.closed:
            return
        try:
            self.s.close()
        finally:
            super().close()
        if self.checked or self.error is not None:     # checked, or its refusal already raised
            return
        if self.n < self.pin['size']:
            self.error = PinError('%s %s: closed after %d of its %d bytes; not checked' % (
                self.what, self.name, self.n, self.pin['size']))
            raise self.error
        self.check()


class Pins:
    """{member: {"size", "sha256"}} for one source; None checks nothing (a local file a maintainer trusts)."""

    def __init__(self, pins, what):
        self.pins, self.what, self.used = pins, what, set()

    def open(self, z, member):
        self.used.add(member)                  # the members a run reads: what a release must pin
        s = z.open(member)
        if self.pins is None:
            return s
        pin = self.pins.get(member)
        if pin is None:
            s.close()
            raise PinError('%s %s is not pinned by the release; it is not read' % (self.what, member))
        return io.BufferedReader(Hashed(s, member, pin, self.what), 8 << 20)

    def read(self, z, member):
        with self.open(z, member) as s:
            return s.read()


def pins_for(zipfile_obj, members, what):
    """{member: {"size", "sha256"}} computed from a trusted local copy (release-cut writes these into release.json)."""
    out = {}
    for m in members:
        h, n = hashlib.sha256(), 0
        with zipfile_obj.open(m) as s:
            for b in iter(lambda: s.read(8 << 20), b''):
                h.update(b); n += len(b)
        out[m] = dict(size=n, sha256=h.hexdigest())
    return out


if __name__ == '__main__':
    import json, sys, zipfile
    if len(sys.argv) < 4 or sys.argv[1] != 'pins':
        raise SystemExit(__doc__)
    print(json.dumps(pins_for(zipfile.ZipFile(sys.argv[2]), sys.argv[3:], 'member'), indent=1, sort_keys=True))
