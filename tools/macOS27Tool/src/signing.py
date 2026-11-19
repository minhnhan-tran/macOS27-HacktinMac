"""Release signatures: SSH signatures (SSHSIG, `ssh-keygen -Y sign`) by an Ed25519 key, checked against the keys hao
carries, in pure Python (no ssh-keygen needed: the Recovery has none).

The owner signs each release offline, with a key file or a hardware wallet through its SSH agent (trezor-agent,
ledger-agent), in the folder release-cut staged:
    ssh-keygen -Y sign -f OWNER.pub -n hao-release release.json SHA256SUMS
which writes release.json.sig and SHA256SUMS.sig. hao verifies release.json.sig against tools/macOS27Tool/data/release-keys.pub
(OpenSSH public key lines, `ssh-ed25519 AAAA... comment` or `sk-ssh-ed25519@openssh.com AAAA... comment`) before it reads anything in release.json, then works only from
the verified bytes (a private copy), so the file cannot change between the check and its use.

  load_keys(path)          {key blob: fingerprint} from ssh-ed25519 / sk-ssh-ed25519 lines (more for a key change)
  verify(data, sig, keys)  (fingerprint, hash) when SIG (an armored SSHSIG) signs DATA in NAMESPACE with one of KEYS
  verified_release(path)   the release's bytes checked, copied to a new private folder: (copy, fingerprint, hash)

The format is OpenSSH's PROTOCOL.sshsig, parsed strictly: version 1, namespace hao-release, empty reserved field,
sha512 or sha256, nothing left over. Two key kinds: ssh-ed25519 (a key file, or a wallet's SSH agent) and
sk-ssh-ed25519@openssh.com (a FIDO security key such as a YubiKey, PROTOCOL.u2f: the authenticator signs
sha256(application) || flags || counter || sha256(SSHSIG data); the touch and PIN flags are both required, as the
release keys are made with -O verify-required). Ed25519 verification is RFC 8032 section 6's
reference code (Simplified BSD, IETF Trust; see NOTICE.md) with the same checks: a point that does not decode, or
S >= L, fails. Verification uses no secret, so it need not run in constant time. `sign`/`secret_to_public` are there for
the tests' own keys only.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
KEYS = HERE.parent / 'data' / 'release-keys.pub'
# a candidate cut's hao (release-cut --candidate) also carries this: the "hao-candidate (not a release key)" key, so
# benches can test a cut before the owner signs it. It is never in the repository; a final cut has release-keys.pub only.
CANDIDATE_KEYS = HERE.parent / 'data' / 'candidate-keys.pub'
CANDIDATE = ' (CANDIDATE key, not a release key)'
# owner waivers: releases accepted without a signature (data/unsigned-releases.json), by version
UNSIGNED_LIST = HERE.parent / 'data' / 'unsigned-releases.json'
UNSIGNED = 'UNSIGNED'
SIG_LIMIT = 4096
RELEASE_LIMIT = 16 << 20


class SignatureError(Exception):
    pass


# --- RFC 8032 section 6 (Ed25519), unchanged in substance -----------------------------------------------------------
# Copyright (c) 2017 IETF Trust and the persons identified as the document authors (S. Josefsson, I. Liusvaara).
# All rights reserved. Redistribution and use in source and binary forms, with or without modification, are permitted
# provided that the following conditions are met: Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer. Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials
# provided with the distribution. Neither the name of Internet Society, IETF or IETF Trust, nor the names of specific
# contributors, may be used to endorse or promote products derived from this software without specific prior written
# permission. THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
# GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
# OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x):
    return pow(x, p - 2, p)


d = -121665 * _inv(121666) % p
_SQRT_M1 = pow(2, (p - 1) // 4, p)


def _sha512_modq(s):
    return int.from_bytes(hashlib.sha512(s).digest(), 'little') % q


def _add(P, Q):
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % p, (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C, D = 2 * P[3] * Q[3] * d % p, 2 * P[2] * Q[2] % p
    E, F, G_, H = B - A, D - C, D + C, B + A
    return (E * F, G_ * H, F * G_, E * H)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _recover_x(y, sign):
    if y >= p:
        return None
    x2 = (y * y - 1) * _inv(d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * _SQRT_M1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


_GY = 4 * _inv(5) % p
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % p)


def _compress(P):
    zi = _inv(P[2])
    x, y = P[0] * zi % p, P[1] * zi % p
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % p)


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def secret_to_public(secret):
    return _compress(_mul(_expand(secret)[0], G))


def sign(secret, msg):
    a, prefix = _expand(secret)
    A = _compress(_mul(a, G))
    r = _sha512_modq(prefix + msg)
    Rs = _compress(_mul(r, G))
    s = (r + _sha512_modq(Rs + A + msg) * a) % q
    return Rs + int.to_bytes(s, 32, 'little')


def ed25519_verify(public, msg, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= q:
        return False
    h = _sha512_modq(signature[:32] + public + msg)
    return _equal(_mul(s, G), _add(R, _mul(h, A)))


# --- SSH signatures (OpenSSH PROTOCOL.sshsig) -------------------------------------------------------------------
NAMESPACE = 'hao-release'
MAGIC = b'SSHSIG'
HASHES = {'sha512': hashlib.sha512, 'sha256': hashlib.sha256}
BEGIN, END = '-----BEGIN SSH SIGNATURE-----', '-----END SSH SIGNATURE-----'


def _string(b):
    return len(b).to_bytes(4, 'big') + b


class _Reader:
    def __init__(self, data, what):
        self.data, self.at, self.what = data, 0, what

    def take(self, n):
        if self.at + n > len(self.data):
            raise SignatureError('%s is cut short' % self.what)
        out = self.data[self.at:self.at + n]
        self.at += n
        return out

    def string(self):
        return self.take(int.from_bytes(self.take(4), 'big'))

    def end(self):
        if self.at != len(self.data):
            raise SignatureError('%s has bytes after its end' % self.what)


ED25519 = b'ssh-ed25519'
SK_ED25519 = b'sk-ssh-ed25519@openssh.com'
KINDS = (ED25519, SK_ED25519)
USER_PRESENT = 0x01
USER_VERIFIED = 0x04          # the key's PIN was given (keys made with -O verify-required)


def parse_key(blob, what='a release key'):
    """(kind, public key, application) of an ssh-ed25519 or sk-ssh-ed25519@openssh.com key blob."""
    r = _Reader(blob, what)
    kind = r.string()
    if kind not in KINDS:
        raise SignatureError('%s is %s, not ssh-ed25519 or sk-ssh-ed25519' % (what, kind.decode('ascii', 'replace')[:40]))
    public = r.string()
    application = r.string() if kind == SK_ED25519 else None
    r.end()
    if len(public) != 32:
        raise SignatureError('%s has %d bytes, not 32' % (what, len(public)))
    return kind, public, application


def parse_signature(blob, kind):
    """(signature, flags, counter) of an SSH signature blob of KIND (flags and counter only for a security key)."""
    r = _Reader(blob, 'the signature')
    if r.string() != kind:
        raise SignatureError('the signature is not of the signing key\'s kind')
    sig = r.string()
    flags = counter = None
    if kind == SK_ED25519:
        flags, counter = r.take(1)[0], int.from_bytes(r.take(4), 'big')
    r.end()
    if len(sig) != 64:
        raise SignatureError('the signature has %d bytes, not 64' % len(sig))
    return sig, flags, counter


def _canonical_b64(text, what):
    """TEXT's bytes when it is base64 in the one canonical spelling (padding and unused bits as encoding gives them)."""
    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError:
        raise SignatureError('%s is not base64' % what)
    if base64.b64encode(raw).decode() != text:
        raise SignatureError('%s is not canonical base64' % what)
    return raw


def small_order(public):
    """Whether PUBLIC decodes to a point of small order (8·A is the neutral point), or does not decode."""
    A = _decompress(public)
    if A is None:
        return True
    P = _mul(8, A)
    return P[0] % p == 0 and (P[1] - P[2]) % p == 0


def fingerprint(blob):
    """SHA256:... of a key blob, as ssh-keygen -l shows it."""
    return 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')


def parse_keys(text):
    """{key blob: fingerprint} from OpenSSH public key lines; blank and # lines are skipped, any other line must be an
    ssh-ed25519 or sk-ssh-ed25519@openssh.com key whose type word matches its blob."""
    keys = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0].encode() not in KINDS:
            raise SignatureError('a release key is not an ssh-ed25519 or sk-ssh-ed25519 public key line')
        blob = _canonical_b64(parts[1], 'a release key')
        kind, public, _ = parse_key(blob)
        if kind != parts[0].encode():
            raise SignatureError('a release key line names another type than its key')
        if small_order(public):
            raise SignatureError('a release key is a weak (small-order) point')
        keys[blob] = fingerprint(blob)
    return keys


def load_keys(path=None, candidates=None):
    """{key blob: fingerprint} of the release keys (PATH, default data/release-keys.pub) and, in a candidate build, the
    candidate keys (data/candidate-keys.pub, when present; their fingerprint carries CANDIDATE). An explicit PATH
    (--keys) is used alone."""
    keys = {}
    if path is None:
        try:
            keys.update(parse_keys(Path(KEYS).read_text()))
        except OSError:
            pass
        cand = Path(candidates or CANDIDATE_KEYS)
        if cand.is_file():
            for blob, fp in parse_keys(cand.read_text()).items():
                keys.setdefault(blob, fp + CANDIDATE)
        if not keys:
            raise SignatureError('this hao carries no release key (%s is missing or empty)' % KEYS)
        return keys
    try:
        text = Path(path).read_text()
    except OSError:
        raise SignatureError('this hao carries no release key (%s)' % path)
    keys = parse_keys(text)
    if not keys:
        raise SignatureError('this hao carries no release key (%s is empty)' % path)
    return keys


def is_unsigned(fingerprint):
    """Whether a fingerprint verified_release() returned is an unsigned release's (owner waiver)."""
    return isinstance(fingerprint, str) and fingerprint.startswith(UNSIGNED)


def is_candidate(fingerprint):
    """Whether a fingerprint verify() returned is a candidate key's."""
    return isinstance(fingerprint, str) and fingerprint.endswith(CANDIDATE)


def armored(text):
    """The SSHSIG blob inside -----BEGIN/END SSH SIGNATURE----- (nothing else in the file but blank lines)."""
    lines = [x.strip() for x in text.strip().splitlines()]
    if len(lines) < 3 or lines[0] != BEGIN or lines[-1] != END:
        raise SignatureError('the signature file is not an SSH signature')
    return _canonical_b64(''.join(lines[1:-1]), 'the signature')


def signed_data(namespace, hash_name, data):
    return (MAGIC + _string(namespace.encode()) + _string(b'') + _string(hash_name.encode())
            + _string(HASHES[hash_name](data).digest()))


def verify(data, sig_text, keys, namespace=NAMESPACE):
    """(fingerprint, hash name) when SIG_TEXT (an armored SSHSIG) signs DATA for NAMESPACE with one of KEYS."""
    r = _Reader(armored(sig_text), 'the signature')
    if r.take(6) != MAGIC:
        raise SignatureError('the signature is not an SSH signature')
    if int.from_bytes(r.take(4), 'big') != 1:
        raise SignatureError('the signature is not SSH signature version 1')
    key_blob = r.string()
    kind, public, application = parse_key(key_blob, 'the signing key')
    ns, reserved, hash_name = r.string(), r.string(), r.string()
    sig, flags, counter = parse_signature(r.string(), kind)
    r.end()
    if ns != namespace.encode():
        raise SignatureError('the signature is for "%s", not "%s"' % (ns.decode('ascii', 'replace')[:40], namespace))
    if reserved:
        raise SignatureError('the signature\'s reserved field is not empty')
    hash_name = hash_name.decode('ascii', 'replace')
    if hash_name not in HASHES:
        raise SignatureError('the signature uses hash %s, not sha512 or sha256' % hash_name[:20])
    if key_blob not in keys:
        raise SignatureError('signed with key %s, which this hao does not trust' % fingerprint(key_blob))
    message = signed_data(namespace, hash_name, data)
    if kind == SK_ED25519:
        if not flags & USER_PRESENT:
            raise SignatureError('the security key signed without a touch (user presence not set)')
        if not flags & USER_VERIFIED:
            raise SignatureError('the security key signed without its PIN (user verification not set)')
        message = (hashlib.sha256(application).digest() + bytes([flags]) + counter.to_bytes(4, 'big')
                   + hashlib.sha256(message).digest())
    if not ed25519_verify(public, message, sig):
        raise SignatureError('the signature does not match the file (changed after signing, or not signed by hao)')
    return keys[key_blob], hash_name


def _read(path, limit, what):
    """PATH's bytes: a regular file that is not a symlink, at most LIMIT bytes, read through the checked descriptor."""
    import stat
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        raise SignatureError('there is no %s (%s)' % (what, path))
    except OSError as x:
        raise SignatureError('%s cannot be read: %s' % (path, x.strerror))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise SignatureError('%s is not a plain file of at most %d bytes' % (path, limit))
        with os.fdopen(fd, 'rb', closefd=False) as f:
            return f.read(limit + 1)
    finally:
        os.close(fd)


def signature_of(path):
    return Path(str(path) + '.sig')


def waiver(version, path=None):
    """The owner's words when release VERSION may be used unsigned (data/unsigned-releases.json), else None."""
    try:
        d = json.loads(Path(path or UNSIGNED_LIST).read_text())
    except (OSError, ValueError):
        return None
    r = d.get('releases') if isinstance(d, dict) else None
    return r.get(version) if isinstance(r, dict) and isinstance(version, str) and isinstance(r.get(version), str) else None


def release_version(data):
    try:
        v = json.loads(data).get('version')
    except (ValueError, AttributeError):
        return None
    return v if isinstance(v, str) else None


def check_sums(path, data):
    """SHA256SUMS beside PATH: its bytes, after checking that PATH's DATA is the file it lists. An unsigned release
    needs it (owner: SHA256SUMS stays required): SignatureError when it is missing or does not list DATA."""
    sums = Path(path).parent / 'SHA256SUMS'
    if not os.path.lexists(sums):
        raise SignatureError('this unsigned release has no SHA256SUMS beside %s: download it with release.json'
                             % Path(path).name)
    raw = _read(sums, 1 << 20, 'SHA256SUMS')
    text = raw.decode('utf-8', errors='replace')
    want = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == Path(path).name:
            want = parts[0].lower()
    if want is None:
        raise SignatureError('SHA256SUMS beside %s does not list it' % Path(path).name)
    if hashlib.sha256(data).hexdigest() != want:
        raise SignatureError('%s is not the file SHA256SUMS lists (changed or damaged)' % Path(path).name)
    return raw


def verified_release(path, keys=None):
    """(private copy of PATH, fingerprint, hash): PATH and PATH.sig read once, verified, and the same bytes
    written with the signature into a new 0700 folder, which every later step reads instead of PATH. A release with no
    signature file at all whose version has an owner waiver (waiver()) is accepted: the fingerprint is then
    "UNSIGNED (owner waiver)", and when SHA256SUMS lies beside it, it must be the file listed there."""
    path = Path(path)
    data = _read(path, RELEASE_LIMIT, 'release file')
    if not os.path.lexists(signature_of(path)):
        reason = waiver(release_version(data))
        if reason:
            sums = check_sums(path, data)
            import safety as SAFE
            folder = Path(tempfile.mkdtemp(prefix='hao-release-'))
            import atexit
            import shutil
            atexit.register(shutil.rmtree, str(folder), True)
            copy = SAFE.create(folder / path.name, data, 0o644)
            SAFE.create(folder / 'SHA256SUMS', sums, 0o644)      # the checked list goes with the copy (the drive)
            return copy, '%s (owner waiver: %s)' % (UNSIGNED, reason), 'sha256 checked against SHA256SUMS'
    sig = _read(signature_of(path), SIG_LIMIT, 'signature beside %s (%s.sig)' % (path.name, path.name))
    kid, hashed = verify(data, sig.decode('utf-8', errors='replace'), load_keys() if keys is None else keys)
    import safety as SAFE
    folder = Path(tempfile.mkdtemp(prefix='hao-release-'))
    import atexit
    import shutil
    atexit.register(shutil.rmtree, str(folder), True)
    copy = SAFE.create(folder / path.name, data, 0o644)
    SAFE.create(signature_of(copy), sig, 0o644)
    return copy, kid, hashed


_VERIFIED = {}


def verified(path, keys=None):
    """The verified private copy of release file PATH for every reader in this run (verified_release() once per path,
    then the same copy): readers use this, never PATH itself. SignatureError when it does not verify."""
    key = os.path.abspath(str(path))
    if key not in _VERIFIED:
        _VERIFIED[key] = verified_release(path, keys)
    return _VERIFIED[key][0]
