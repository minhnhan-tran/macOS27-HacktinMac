"""native-identities: which native program the image re-signed ad hoc stands for which Apple-signed original.

  native_identities.py scan SYSTEM --source ROOT [--source ROOT ...]            print the rows and what was skipped
  native_identities.py install SYSTEM --source ROOT [--source ROOT ...]         install originals, list, record

Every recipe that edits a native program re-signs it ad hoc, which loses the program's Apple signature, so a requirement
such as `anchor apple` fails for it. macos27tool-runtime's libm27_codeid (carried by the image's x86_64 Security.framework,
golden-gate-userspace) answers such a failure from an Apple-signed reference named in /usr/lib/m27/native-identities
(macos27tool-runtime include/m27/idlist.h):

    <observed cdhash>  <reference cdhash>  <arch>  <reference path relative to />

This walks the finished System for main executables (MH_EXECUTE) whose x86_64 slice is signed ad hoc and finds, at the
same path in a source root (the build's pristine 27.x System, the Tahoe native donors), the Apple-signed x86_64 slice it
was made from:

- the source's slice is not ad hoc and has the same signing identifier;
- it is the same program: at least 90% of the original's signed bytes after the load commands, compared in 64-byte
  pieces at the same file offsets, are unchanged (an inserted load command or a replaced Info.plist section moves
  nothing);
- installed under /usr/lib/m27/originals/<path> (root:wheel 0644, directories 0755), the copy passes
  `codesign --verify --arch x86_64 -R='anchor apple'`.

Only the programs in programs.txt (next to this file) get a row, and only when their load commands differ from the
original's by exactly the libraries listed for them: an edited program gets an Apple identity only by a reviewed line.
A program re-signed ad hoc that is not listed but would otherwise match its original stops `install`.

Programs listed in m27's stub-identities, and any program for which no such original exists (OCLP's own programs, for
example), get no row; `scan` says why. A program that is still Apple-signed needs no row and is not touched.
"""
import argparse
import collections
import hashlib
import json
import os
import plistlib
import re
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import time

CPU_X86_64 = 0x01000007
LIST = 'usr/lib/m27/native-identities'
STUBS = 'usr/lib/m27/stub-identities'
ORIGINALS = 'usr/lib/m27/originals'
RECORD = 'System/Library/CoreServices/golden-gate-native-identities.json'
PROGRAMS = Path(__file__).resolve().parent/'programs.txt'
DYLIB_COMMANDS = {0xc, 0x80000018, 0x8000001f, 0x20, 0x80000023}   # LOAD, LOAD_WEAK, REEXPORT, LAZY_LOAD, LOAD_UPWARD
HASHES = {1: hashlib.sha1, 2: hashlib.sha256, 3: hashlib.sha256, 4: hashlib.sha384}
RANK = {1: 1, 3: 2, 2: 3, 4: 4}                       # the kernel's preference among CodeDirectories
CS_ADHOC = 0x2
PIECE = 64
# what the last candidates() walk did, for the log (main): entries listed, executables whose headers were read, seconds
WALK = dict(entries=0, executables=0, seconds=0.0)


def slices(data):
    """[(cputype, offset, size)] of a thin or fat Mach-O; [] if not Mach-O."""
    magic = data[:4]
    if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        return [(struct.unpack_from('<i', data, 4)[0], 0, len(data))]
    if magic in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
        wide = magic == b'\xca\xfe\xba\xbf'
        count = struct.unpack_from('>I', data, 4)[0]
        if count > 32:
            return []
        out = []
        for k in range(count):
            if wide:
                cpu, _, offset, size = struct.unpack_from('>iiQQ', data, 8 + 32 * k)
            else:
                cpu, _, offset, size = struct.unpack_from('>iiII', data, 8 + 20 * k)
            out.append((cpu, offset, size))
        return out
    return []


class Slice:
    """One slice's bytes, data[offset:offset + size] of the whole file, read on demand: a range is served from the
    loaded prefix (the header and load commands) or read from the open file, so a signature costs the header, the
    load commands and the code signature blob instead of the whole file. get(start, stop) is exactly piece[start:stop]
    of the slice's bytes (cut to the slice and to the file)."""

    def __init__(self, stream, length, offset, size, prefix=4096):
        self.stream, self.offset = stream, offset
        self.size = max(0, min(offset + size, length) - offset) if offset < length else 0
        self.prefix = self.read(0, min(prefix, self.size))

    @classmethod
    def of_bytes(cls, data, offset, size):
        piece = cls.__new__(cls)
        piece.stream, piece.offset, piece.prefix = None, 0, data[offset:offset + size]
        piece.size = len(piece.prefix)
        return piece

    def read(self, start, stop):
        start, stop = min(start, self.size), min(stop, self.size)
        if stop <= start:
            return b''
        self.stream.seek(self.offset + start)
        return self.stream.read(stop - start)

    def get(self, start, stop):
        if stop <= len(self.prefix) or self.stream is None:
            return self.prefix[start:stop]
        return self.read(start, stop)

    def load(self, stop):
        """Keep [0, stop) in memory (the load commands, read once)."""
        if self.stream is not None and stop > len(self.prefix):
            self.prefix = self.read(0, stop)


def signature(data, offset, size):
    """dict(filetype, adhoc, cdhash, identifier, limit) of a 64-bit slice, or None if unsigned/unsupported."""
    return slice_signature(Slice.of_bytes(data, offset, size))


def slice_signature(piece):
    """signature() of a Slice: reads its header, load commands and LC_CODE_SIGNATURE blob only."""
    if piece.size < 32 or piece.get(0, 4) != b'\xcf\xfa\xed\xfe':
        return None
    filetype, count, commands = struct.unpack_from('<III', piece.get(12, 24))
    if commands > MAX_COMMANDS or 32 + commands > piece.size or count > commands // 8:
        return None
    piece.load(32 + commands)
    at, blob = 32, None
    for _ in range(count):
        command, length = struct.unpack_from('<II', piece.get(at, at + 8))
        if length < 8 or at + length > 32 + commands:
            return None
        if command == 0x1d:                             # LC_CODE_SIGNATURE (one: a second makes it unreadable)
            if blob is not None:
                return None
            dataoff, datasize = struct.unpack_from('<II', piece.get(at + 8, at + 16))
            if datasize > MAX_SIGNATURE or dataoff + datasize > piece.size:
                return None
            blob = piece.get(dataoff, dataoff + datasize)
        at += length
    if not blob or blob[:4] != b'\xfa\xde\x0c\xc0':
        return None
    best, cms = None, False
    count = struct.unpack_from('>I', blob, 8)[0]
    if count > 64 or 12 + 8 * count > len(blob):
        return None
    for k in range(count):
        kind, at = struct.unpack_from('>II', blob, 12 + 8 * k)
        if at + 8 > len(blob):
            return None
        magic, length = struct.unpack_from('>II', blob, at)
        if at + length > len(blob):
            return None
        if kind == 0x10000 and length > 8:
            cms = True
        if magic == 0xfade0c02:
            cd = blob[at:at + length]
            flags, _, ident_offset = struct.unpack_from('>III', cd, 12)
            limit = struct.unpack_from('>I', cd, 32)[0]
            hash_type = cd[37]
            if hash_type in RANK and (best is None or RANK[hash_type] > RANK[best[0]]):
                best = (hash_type, cd, flags, ident_offset, limit)
    if not best:
        return None
    hash_type, cd, flags, ident_offset, limit = best
    return dict(filetype=filetype, adhoc=bool(flags & CS_ADHOC) or not cms,
                cdhash=HASHES[hash_type](cd).hexdigest()[:40],
                identifier=cd[ident_offset:cd.index(b'\0', ident_offset)].decode(errors='replace'), limit=limit)


def x86_64(path):
    """(slice signature, slice bytes) of the file's x86_64 slice, or (None, None)."""
    found = x86_64_signature(path)
    if found is None:
        return None, None
    return found[0], slice_bytes(path, found)


def x86_64_signature(path):
    """(signature or None, offset, size) of the file's x86_64 slice, or None: x86_64() without reading the slice.

    Reads the fat header, the slice's Mach-O header and load commands and its code signature blob, nothing else; the
    slice's bytes (slice_bytes) are read only for a program that has an original to compare with."""
    try:
        with open(path, 'rb') as stream:
            head = stream.read(4096)
            if head[:4] not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
                return None
            length = os.fstat(stream.fileno()).st_size
            if head[:4] == b'\xcf\xfa\xed\xfe':
                found = [(struct.unpack_from('<i', head, 4)[0], 0, length)]
            else:
                found = slices(head)                    # a fat header (at most 32 entries) fits in 4 KiB
            matches = [(offset, size) for cpu, offset, size in found if cpu == CPU_X86_64]
            if len(matches) > 1:
                return None, -1, 0                       # two x86_64 slices: ambiguous, never enrolled
            for offset, size in matches:
                return slice_signature(Slice(stream, length, offset, size)), offset, size
    except (OSError, ValueError, struct.error, IndexError, UnicodeDecodeError):
        return None
    return None


def slice_bytes(path, found):
    """The x86_64 slice's bytes, data[offset:offset + size] of the whole file, for x86_64_signature's result."""
    _, offset, size = found
    with open(path, 'rb') as stream:
        piece = Slice(stream, os.fstat(stream.fileno()).st_size, offset, size, prefix=0)
        return piece.read(0, piece.size)


def load_programs(path=PROGRAMS):
    """programs.txt -> {relative path: dict(recipe, libs, refs, digests, ents, policies, builds)}: the inserted library
    paths (+PATH) and, for each input build, one line's tuple (ref, digest, ents, policy) in builds; refs, digests,
    ents and policies are the sets of their values. A program has one line per input build; every line of a program
    names the same recipe and libraries."""
    out = {}
    for line in Path(path).read_text().splitlines():
        words = re.split(r'\s{2,}|\t', line.split('#', 1)[0].strip()) if line.split('#', 1)[0].strip() else []
        if not words:
            continue
        libs, one = set(), dict(refs=[], digests=[], ents=[], policies=[])
        for w in words[2:]:
            kind = {'+': 'libs', '=': 'refs', '~': 'digests', '%': 'ents', '!': 'policies'}.get(w[:1])
            shape = {'libs': r'/\S.*', 'refs': r'[0-9a-f]{40}'}.get(kind, r'[0-9a-f]{64}')
            if not kind or not re.fullmatch(shape, w[1:]):
                raise ValueError(f'{path}: bad field {w!r}: {line}')
            (libs.add if kind == 'libs' else one[kind].append)(w[1:])
        if len(words) < 3 or any(len(v) != 1 for v in one.values()):
            raise ValueError(f'{path}: bad line (needs one each of =CDHASH ~DIGEST %ENTITLEMENTS !POLICY): {line}')
        entry = out.setdefault(words[0], dict(recipe=words[1], libs=frozenset(libs), builds=frozenset()))
        if entry['recipe'] != words[1] or entry['libs'] != libs:
            raise ValueError(f'{path}: lines for {words[0]} differ in recipe or libraries')
        entry['builds'] = entry['builds'] | {(one['refs'][0], one['digests'][0], one['ents'][0], one['policies'][0])}
    for entry in out.values():
        for n, kind in enumerate(('refs', 'digests', 'ents', 'policies')):
            entry[kind] = frozenset(b[n] for b in entry['builds'])
    return out


def dylibs(data):
    """The library paths a thin 64-bit slice's load commands name (LC_LOAD_DYLIB and its kinds), as a sorted list of
    (command, path): a load that changes kind (weak, re-export, upward) counts as another library."""
    out = []
    for command, at, length in _commands(data):
        if command in DYLIB_COMMANDS:
            name = struct.unpack_from('<I', data, at + 8)[0]
            if length < 24 or not 24 <= name < length or b'\0' not in data[at + name:at + length]:
                raise ValueError('bad dylib load command')
            out.append((command, data[at + name:at + length].split(b'\0', 1)[0].decode()))
    return sorted(out)


def _commands(data):
    """[(command, offset, length)] of a thin 64-bit slice's load commands, every one inside sizeofcmds."""
    if len(data) < 32 or data[:4] != b'\xcf\xfa\xed\xfe':
        raise ValueError('not a 64-bit Mach-O slice')
    count, size = struct.unpack_from('<II', data, 16)
    at, end, out = 32, 32 + size, []
    if end > len(data):
        raise ValueError('load commands run past the slice')
    for _ in range(count):
        if at + 8 > end:
            raise ValueError('load commands run past sizeofcmds')
        command, length = struct.unpack_from('<II', data, at)
        if length < 8 or length % 8 or at + length > end:
            raise ValueError('bad load command')
        out.append((command, at, length))
        at += length
    return out


def content_digest(data):
    """sha256 of a slice's bytes up to its code signature, with the fields signing rewrites zeroed (the signature's
    size, __LINKEDIT's sizes): the same for the unsigned edit output and for it signed ad hoc by any codesign."""
    data = bytearray(data)
    dataoff = None
    for command, at, length in _commands(data):
        if command == 0x1d:                                         # LC_CODE_SIGNATURE: dataoff, datasize
            dataoff = struct.unpack_from('<I', data, at + 8)[0]
            struct.pack_into('<I', data, at + 12, 0)
        elif command == 0x19 and data[at + 8:at + 24].rstrip(b'\0') == b'__LINKEDIT':
            struct.pack_into('<Q', data, at + 32, 0)                   # vmsize
            struct.pack_into('<Q', data, at + 48, 0)                   # filesize
    if dataoff is None or dataoff > len(data):
        raise ValueError('no code signature')
    return hashlib.sha256(bytes(data[:dataoff])).hexdigest()


MAX_COMMANDS, MAX_SIGNATURE, MAX_DEPTH = 1 << 20, 1 << 24, 32
AD_HOC_SLOTS = {0, 2, 5, 7, 0x10000}                       # what codesign writes for an ad hoc signature with entitlements
EMPTY_CMS = bytes.fromhex('fade0b0100000008')


def _der(data, at, end, depth=0):
    """(typed value, next offset) of one DER item in data[at:end], strictly: minimal lengths, items inside their
    container, BOOLEAN 00/FF, minimal INTEGER, dictionaries with unique UTF8String keys. Values are typed
    (('b', bool), ('i', int), ('s', str), ('a', [...]), ('d', {...})) so a boolean never equals an integer."""
    if depth > MAX_DEPTH or at + 2 > end:
        raise ValueError('DER too deep or truncated')
    tag, length, at = data[at], data[at + 1], at + 2
    if length & 0x80:
        n = length & 0x7f
        if not 1 <= n <= 3 or at + n > end or data[at] == 0:
            raise ValueError('bad DER length')
        length, at = int.from_bytes(data[at:at + n], 'big'), at + n
        if length < 0x80 or length < 1 << (8 * (n - 1)):
            raise ValueError('DER length not minimal')
    stop = at + length
    if stop > end:
        raise ValueError('DER item runs past its container')
    body = data[at:stop]
    if tag == 0x0c:
        return ('s', body.decode()), stop
    if tag == 0x01:
        if body not in (b'\0', b'\xff'):
            raise ValueError('bad DER BOOLEAN')
        return ('b', body == b'\xff'), stop
    if tag == 0x02:
        if not body or (len(body) > 1 and (body[0] == 0 and body[1] < 0x80 or body[0] == 0xff and body[1] >= 0x80)):
            raise ValueError('bad DER INTEGER')
        return ('i', int.from_bytes(body, 'big', signed=True)), stop
    if tag in (0x30, 0xb0):
        items, p = [], at
        while p < stop:
            item, p = _der(data, p, stop, depth + 1)
            items.append(item)
        if tag == 0x30:
            return ('a', items), stop
        out = {}
        for item in items:
            pair = item[1] if item[0] == 'a' else None
            if not pair or len(pair) != 2 or pair[0][0] != 's' or pair[0][1] in out:
                raise ValueError('bad DER dictionary entry')
            out[pair[0][1]] = pair[1]
        return ('d', out), stop
    raise ValueError(f'unsupported DER tag {tag:#x}')


def _typed(value, depth=0):
    """A property-list value as _der types it."""
    if depth > MAX_DEPTH:
        raise ValueError('entitlements too deep')
    if isinstance(value, bool):
        return ('b', value)
    if isinstance(value, int):
        return ('i', value)
    if isinstance(value, str):
        return ('s', value)
    if isinstance(value, list):
        return ('a', [_typed(v, depth + 1) for v in value])
    if isinstance(value, dict):
        return ('d', {k: _typed(v, depth + 1) for k, v in value.items()})
    raise ValueError(f'unsupported entitlement value {type(value).__name__}')


def signature_profile(data):
    """The one signature layout this accepts, read strictly, or ValueError:
    exactly one LC_CODE_SIGNATURE and one __LINKEDIT, the signature at the end of the slice, __LINKEDIT ending with it (vmsize its filesize rounded to 4 or 16 KiB); the SuperBlob's blobs
    contiguous right after its index, in the slots codesign writes for an ad hoc signature with entitlements and no
    other: one SHA-256 CodeDirectory (so nothing chooses between directories), requirements, empty CMS, XML and
    DER entitlements that agree by type. Returns dict(cdhash, policy, entitlements): policy is a sha256 of the
    CodeDirectory's every field but its hashes (version, flags, slot counts, code limit, hash and page size,
    platform, execution segment, runtime, identifier, every special-slot hash: -2, -5 and -7 must hash the
    requirements and entitlements blobs, the others but -1 must be empty) and of the requirements blob;
    entitlements a sha256 of the typed entitlements; slack the bytes between the SuperBlob and the end of the signature
    space, which slack_problem() requires to be exactly the original's bytes there (how much codesign reserves differs
    between Macs, so the policy leaves them out)."""
    cmds = _commands(data)
    sig = [at for command, at, length in cmds if command == 0x1d and length == 16]
    link = [at for command, at, length in cmds
            if command == 0x19 and length >= 72 and data[at + 8:at + 24].rstrip(b'\0') == b'__LINKEDIT']
    if len(sig) != 1 or len(link) != 1 or sum(1 for c, _, _ in cmds if c == 0x1d) != 1:
        raise ValueError('not exactly one code signature and one __LINKEDIT')
    dataoff, datasize = struct.unpack_from('<II', data, sig[0] + 8)
    vmsize, fileoff, filesize = struct.unpack_from('<QQQ', data, link[0] + 32)
    if datasize > MAX_SIGNATURE or dataoff + datasize != len(data) or fileoff + filesize != len(data) \
            or not fileoff <= dataoff or vmsize not in ((filesize + 0xfff) & ~0xfff, (filesize + 0x3fff) & ~0x3fff):
        raise ValueError('signature or __LINKEDIT not where codesign puts them')
    blob = data[dataoff:]
    magic, total, count = struct.unpack_from('>III', blob, 0)
    if magic != 0xfade0cc0 or total > datasize or count != len(AD_HOC_SLOTS):
        raise ValueError('not an ad hoc SuperBlob')
    blobs, at = {}, 12 + 8 * count
    for k in range(count):
        kind, off = struct.unpack_from('>II', blob, 12 + 8 * k)
        if off != at or off + 8 > total or kind in blobs:
            raise ValueError('SuperBlob blobs not contiguous after the index')
        size = struct.unpack_from('>I', blob, off + 4)[0]
        if size < 8 or off + size > total:
            raise ValueError('bad SuperBlob blob')
        blobs[kind], at = bytes(blob[off:off + size]), off + size
    if at != total or set(blobs) != AD_HOC_SLOTS:
        raise ValueError(f'SuperBlob slots {sorted(blobs)}, not {sorted(AD_HOC_SLOTS)}')
    if blobs[0x10000] != EMPTY_CMS or blobs[2][:4] != b'\xfa\xde\x0c\x01':
        raise ValueError('CMS not the empty ad hoc one, or no requirements blob')
    cd = blobs[0]
    if cd[:4] != b'\xfa\xde\x0c\x02' or len(cd) < 88:
        raise ValueError('bad CodeDirectory')
    version, flags, hash_offset, ident_offset, n_special, n_code, limit = struct.unpack_from('>IIIIIII', cd, 8)
    hash_size, hash_type, platform, page = cd[36], cd[37], cd[38], cd[39]
    if version not in (0x20400, 0x20500, 0x20600) or not flags & CS_ADHOC or hash_type != 2 or hash_size != 32 \
            or page != 12 or limit != dataoff or n_code != (limit + 4095) // 4096 or n_special < 7 \
            or hash_offset != len(cd) - 32 * n_code or hash_offset < 32 * n_special or not 88 <= ident_offset < hash_offset:
        raise ValueError('CodeDirectory is not a plain SHA-256 ad hoc one covering the whole slice')
    ident = cd[ident_offset:cd.index(b'\0', ident_offset, hash_offset)]
    special = {i: cd[hash_offset - 32 * i:hash_offset - 32 * (i - 1)] for i in range(1, n_special + 1)}
    for slot, kind in ((2, 2), (5, 5), (7, 7)):                 # requirements, XML and DER entitlements: bound here
        if special[slot] != hashlib.sha256(blobs[kind]).digest():
            raise ValueError(f'special slot -{slot} does not hash its blob')
    for slot in range(3, n_special + 1):                        # resources, app-specific, DMG and beyond: absent
        if slot not in (5, 7) and special[slot] != bytes(32):
            raise ValueError(f'special slot -{slot} is not empty')
    fixed = cd[8:hash_offset]                    # every field, the identifier and every special slot (-1 Info.plist)
    xml, der = blobs[5], blobs[7]
    if xml[:4] != b'\xfa\xde\x71\x71' or der[:4] != b'\xfa\xde\x71\x72':
        raise ValueError('bad entitlements blobs')
    from_xml = _typed(plistlib.loads(xml[8:]))
    if len(der) < 10 or der[8] != 0x70:                      # [APPLICATION 16] { INTEGER 1, [16] dictionary }
        raise ValueError('bad DER entitlements')
    inner, end = _der(bytes([0x30]) + der[9:], 0, len(der) - 8)
    if end != len(der) - 8 or inner[0] != 'a' or len(inner[1]) != 2 or inner[1][0] != ('i', 1) \
            or inner[1][1][0] != 'd':
        raise ValueError('bad DER entitlements')
    if inner[1][1] != from_xml:
        raise ValueError('DER and XML entitlements differ')
    return dict(cdhash=hashlib.sha256(cd).hexdigest()[:40], identifier=ident.decode(),
                policy=hashlib.sha256(fixed + b'\0requirements\0' + blobs[2]).hexdigest(),
                slack=(dataoff + total, bytes(blob[total:])),
                entitlements=hashlib.sha256(json.dumps(from_xml, sort_keys=True).encode()).hexdigest())


def slack_problem(profile, reference):
    """Why the signature's slack is not exactly the one this layout allows, or None. codesign leaves the input's old
    signature where its new one is shorter than the space it reserves, and the input's old signature is the original's
    (the recipes edit only load commands and code). So the slack must be the original's x86_64-slice bytes at the same
    offsets, byte for byte, and zeros past the original's end: one value for a given layout, nothing to choose. A byte
    zeroed where the original has another is refused, like any other change."""
    start, slack = profile['slack']
    expected = reference[start:start + len(slack)]
    expected += bytes(len(slack) - len(expected))
    if slack != expected:
        at = next(i for i in range(len(slack)) if slack[i] != expected[i])
        return f'signature slack at offset {start + at:#x} is not the original\'s byte there (or zero past its end)'
    return None


def verify_observed(path):
    """codesign checks the x86_64 slice's signature against its bytes; returns (ok, why, the CDHash it reports)."""
    result = subprocess.run(['codesign', '--verify', '--strict', '--arch', 'x86_64', str(path)],
                            capture_output=True, text=True)
    if result.returncode:
        return False, result.stderr.strip(), None
    shown = subprocess.run(['codesign', '-dvvv', '--arch', 'x86_64', str(path)], capture_output=True, text=True).stderr
    cdhash = next((line.split('=', 1)[1].strip() for line in shown.splitlines() if line.startswith('CDHash=')), None)
    return True, '', cdhash


def same_program(observed, reference, limit):
    """At least 90% of the reference's signed bytes after the load commands, compared in 64-byte pieces at the same
    offsets, are unchanged. The Mach-O header and load commands are left out: re-signing rewrites them, and an
    inserted load command takes the padding after them."""
    limit = min(limit, len(reference))
    start = 32 + max(struct.unpack_from('<I', observed, 20)[0], struct.unpack_from('<I', reference, 20)[0])
    start = (start + PIECE - 1) // PIECE * PIECE
    pieces = max(1, (limit - start + PIECE - 1) // PIECE)
    same = sum(1 for at in range(start, limit, PIECE)
               if observed[at:min(at + PIECE, limit)] == reference[at:min(at + PIECE, limit)])
    return same * 10 >= pieces * 9, f'{same}/{pieces}'


def listed_stubs(system):
    path = Path(system)/STUBS
    if not path.is_file():
        return set()
    return {line.split()[0].lower() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith('#')}


def candidates(system):
    """(relative path, signature, slice) of the executables in SYSTEM whose x86_64 slice is an ad hoc signed
    MH_EXECUTE; slice is x86_64_signature's result (slice_bytes reads the bytes when they are needed)."""
    system = Path(system)
    skip = {system/ORIGINALS}
    started = time.monotonic()
    WALK.update(entries=0, executables=0, seconds=0.0)
    for folder, dirs, names in os.walk(system):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if here/d not in skip and not (here/d).is_symlink())
        WALK['entries'] += len(dirs) + len(names)
        for name in sorted(names):
            path = here/name
            try:
                info = path.lstat()
            except OSError:
                continue
            # a regular file by lstat: is_file() and not is_symlink(), without two more stat calls
            if not (info.st_mode & 0o111) or not stat.S_ISREG(info.st_mode) or info.st_size < 4096:
                continue
            WALK['executables'] += 1
            found = x86_64_signature(path)
            sig = found and found[0]
            if sig and sig['filetype'] == 2 and sig['adhoc']:
                WALK['seconds'] += time.monotonic() - started
                yield path.relative_to(system).as_posix(), sig, found
                started = time.monotonic()
    WALK['seconds'] += time.monotonic() - started


def scan(system, sources, programs=None):
    """(rows, skipped, refused): rows = [dict(path, observed, reference, source, same)], skipped = {path: reason},
    refused = {path: reason} (would match an original but is not in PROGRAMS, or inserts other libraries than listed).
    PROGRAMS defaults to programs.txt."""
    programs = load_programs() if programs is None else programs
    stubs = listed_stubs(system)
    rows, skipped, refused = [], {}, {}
    system = Path(system)
    for rel, sig, found in candidates(system):
        if sig['cdhash'] in stubs:
            skipped[rel] = 'm27 stub (stub-identities)'
            continue
        reasons = []
        for source in sources:
            original = Path(source)/rel
            if not original.is_file() or original.is_symlink():
                continue
            ref_found = x86_64_signature(original)
            ref = ref_found and ref_found[0]
            if not ref:
                reasons.append(f'{source}: no signed x86_64 slice')
            elif ref['adhoc']:
                reasons.append(f'{source}: not Apple-signed')
            elif ref['identifier'] != sig['identifier']:
                reasons.append(f'{source}: identifier {ref["identifier"]} != {sig["identifier"]}')
            elif ref['cdhash'] == sig['cdhash']:
                reasons.append(f'{source}: same cdhash')
            else:
                observed, reference = slice_bytes(system/rel, found), slice_bytes(original, ref_found)
                same, measure = same_program(observed, reference, ref['limit'])
                if not same:
                    reasons.append(f'{source}: another program ({measure} code blocks unchanged)')
                    continue
                if rel not in programs:
                    refused[rel] = f'matches {original} ({measure} unchanged) but is not in programs.txt'
                    break
                want = programs[rel]
                if ref['cdhash'] not in want['refs']:
                    refused[rel] = (f'its original {original} has cdhash {ref["cdhash"]}, not one programs.txt pins '
                                    f'({", ".join(sorted(want["refs"]))})')
                    break
                ok, why, shown = verify_observed(system/rel)
                if not ok:
                    refused[rel] = f'its own signature does not verify against its bytes: {why}'
                    break
                try:
                    profile = signature_profile(observed)
                    seen = (ref['cdhash'], content_digest(observed), profile['entitlements'], profile['policy'])
                    have, had = collections.Counter(dylibs(observed)), collections.Counter(dylibs(reference))
                except (ValueError, struct.error, IndexError, UnicodeDecodeError, plistlib.InvalidFileException) as e:
                    refused[rel] = f'its signature is not the plain ad hoc one this accepts: {e}'
                    break
                if not (profile['cdhash'] == sig['cdhash'] == shown) or profile['identifier'] != sig['identifier']:
                    refused[rel] = (f'its CodeDirectory is ambiguous: parsed {profile["cdhash"]}, selected '
                                    f'{sig["cdhash"]}, codesign reports {shown}')
                    break
                problem = slack_problem(profile, reference)
                if problem:
                    refused[rel] = problem
                    break
                if seen not in want['builds']:
                    refused[rel] = (f'(original, content digest, entitlements, signature policy) = {seen} is not a '
                                    f'line programs.txt pins ({want["recipe"]})')
                    break
                added, removed = have - had, had - have
                if removed or added != collections.Counter((0xc, lib) for lib in want['libs']):
                    refused[rel] = (f'its libraries differ from {original}: adds {sorted(added) or "none"}, removes '
                                    f'{sorted(removed) or "none"}; programs.txt allows {sorted(want["libs"]) or "none"} '
                                    f'({want["recipe"]})')
                    break
                rows.append(dict(path=rel, observed=sig['cdhash'], reference=ref['cdhash'], source=str(original),
                                 identifier=sig['identifier'], same=measure))
                break
        else:
            skipped[rel] = '; '.join(reasons) or 'no original at this path in any source'
    # a listed program that its recipe re-signed must get its row: any other outcome fails, never a silent omission
    enrolled = {r['path'] for r in rows}
    for rel in programs:
        if rel in enrolled or rel in refused or not (system/rel).is_file():
            continue
        found = x86_64_signature(system/rel)
        if found is None or found[0] is None or found[0]['adhoc'] or not verify(system/rel)[0]:
            refused[rel] = ('listed but not enrolled, and not an Apple-signed program either: ' +
                            skipped.get(rel, 'its x86_64 slice or signature is unreadable'))
    return rows, skipped, refused


def verify(path):
    result = subprocess.run(['codesign', '--verify', '--arch', 'x86_64', '-R=anchor apple', str(path)],
                            capture_output=True, text=True)
    return result.returncode == 0, result.stderr.strip()


def _real_folders(system, folder, owner):
    """Make FOLDER under SYSTEM (0755, OWNER) where missing; every existing part below SYSTEM must be a directory,
    never a link, so nothing written below it lands elsewhere."""
    system, part = Path(system), Path(system)
    for name in Path(folder).relative_to(system).parts:
        part = part/name
        try:
            info = part.lstat()
        except FileNotFoundError:
            part.mkdir(mode=0o755)
            os.chown(part, *owner, follow_symlinks=False)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise SystemExit(f'native-identities: {part} is not a directory (a link or a file); nothing written')


def _write_new(path, data, owner):
    """Write DATA as PATH (0644, OWNER), never through a link or into a file with other names."""
    if path.is_symlink():
        raise SystemExit(f'native-identities: {path} is a link; nothing written')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o644)          # truncated only once checked
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink != 1:
            raise SystemExit(f'native-identities: {path} is not a plain file of its own; nothing written')
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fchown(fd, *owner)
        os.fchmod(fd, 0o644)
    finally:
        os.close(fd)


def install(system, sources, owner=(0, 0), programs=None):
    """Copy each row's original under usr/lib/m27/originals, check it, write the list and a record."""
    system = Path(system)
    rows, skipped, refused = scan(system, sources, programs)
    if refused:
        raise SystemExit('native-identities: refused (no Apple identity without a programs.txt line):\n' +
                         '\n'.join(f'  {p}: {why}' for p, why in sorted(refused.items())))
    _real_folders(system, system/'usr/lib/m27', owner)
    kept = []
    for row in rows:
        copy = system/ORIGINALS/row['path']
        _real_folders(system, copy.parent, owner)
        _write_new(copy, Path(row['source']).read_bytes(), owner)
        ok, why = verify(copy)
        if not ok:
            copy.unlink()
            raise SystemExit(f'native-identities: the original of {row["path"]} fails anchor apple: {why}')
        kept.append(row)
    text = ['# native-identities: native programs this image re-signed ad hoc -> their Apple-signed originals',
            '# (golden-gate-native-identities; macos27tool-runtime include/m27/idlist.h). Generated; do not edit.']
    text += [f'{r["observed"]}  {r["reference"]}  x86_64  {ORIGINALS}/{r["path"]}' for r in kept]
    target = system/LIST
    _write_new(target, ('\n'.join(text) + '\n').encode(), owner)
    record = dict(list=LIST, rows=kept, skipped=skipped, sources=[str(s) for s in sources],
                  list_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('scan', 'install'))
    parser.add_argument('system', type=Path)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--record', type=Path, help='where install writes its record (default: SYSTEM/' + RECORD + ')')
    args = parser.parse_args()
    if args.command == 'scan':
        rows, skipped, refused = scan(args.system, args.source)
        print(json.dumps(dict(rows=rows, skipped=skipped, refused=refused), indent=2))
        return
    started = time.monotonic()
    record = install(args.system, args.source)
    print(f'native-identities: walk {WALK["seconds"]:.1f} s ({WALK["entries"]} entries, {WALK["executables"]} '
          f'executables\' headers read), install {time.monotonic() - started:.1f} s in all', flush=True)
    path = args.record or args.system/RECORD
    if args.record is None:                     # inside the image: only through real folders, never through a link
        _real_folders(args.system, path.parent, (os.getuid(), os.getgid()))
        _write_new(path, (json.dumps(record, indent=2) + '\n').encode(), (os.getuid(), os.getgid()))
    else:
        path.write_text(json.dumps(record, indent=2) + '\n')
        os.chmod(path, 0o644)
    for row in record['rows']:
        print('native identity:', row['path'], row['observed'], '->', row['reference'], f'({row["same"]} unchanged)',
              flush=True)
    print(f'native-identities: {len(record["rows"])} rows, {len(record["skipped"])} ad hoc programs without one',
          flush=True)


if __name__ == '__main__':
    sys.exit(main())
