"""Keep Swift prespecialized metadata on when shared-cache images are overridden on disk.

libswiftCore turns prespecialized metadata off for the whole process when
`_dyld_shared_cache_some_image_overridden()` is true. On m27 images dyld counts
every file at a cache install path as an override (golden-gate-dyld-internal
makes dyld an internal install), including Apple's own libsystem files. The
switch is conservative: an entry is only wrong if its key or value lies in an
overridden image. This module

- finds the two libswiftCore call sites that make that switch (the
  image-loaded hook and the state setup; each stores mapConfiguration 4) by
  their bytes, never by a fixed address;
- gates: refuses if any pointer-keyed metadata entry (Apple's and any catalog
  keys given) has a descriptor, argument or value in an overridden image;
- nulls each descriptor-map slot whose descriptor lies in an overridden image
  (a null slot reads as empty, so those names take the runtime's normal path);
- with `--conformance` (V2), also makes the protocol-conformance call false
  (cache images' __swift5_proto sections are then not scanned; dyld's tables
  answer for them, while images loaded from disk are still scanned), after
  removing from dyld's type, metadata and foreign conformance tables every key
  whose type, metadata or protocol lies in an overridden image (its bucket
  becomes the sentinel, so the lookup reports not found). Groups whose key is
  clean but that hold a conformance in an overridden image are removed when
  every row is such; mixed groups stay, since dyld returns only a duplicate
  from a loaded image and an overridden image's cache copy is never loaded.
  Without the option that site is left alone.

Edits are emitted in m27-patcher's cache-edit shape (file offset, before,
after, keyed by the input file's sha256); `--apply` writes edited copies and
re-signs them with 4 KiB pages for a live swap.
"""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import struct

from .candidate import CandidateFamily
from .codesign import emit as emit_signature
from .exports import require
from .image_indexes import descriptors, swift_roots
from .objc_emit import FamilyReader
from .runtime_probes import cached_symbols
from .slides import family_slides, pointer_at
from .swift_metadata import References, pointer_entries
from .swift_refs import read_table

SWIFT_CORE = '/usr/lib/swift/libswiftCore.dylib'
LIBDYLD = '/usr/lib/system/libdyld.dylib'
OVERRIDDEN = '_dyld_shared_cache_some_image_overridden'
NOP_CALL = bytes.fromhex('31c00f1f00')   # xor eax,eax; nop dword [rax]
# The bytes after `call stub` that identify each site (test al,al; je rel8; then the store of 4).
IMAGE_LOADED = re.compile(rb'\x84\xc0\x74.\x48\x83\x3d.{5}\x75.\xc7\x05.{4}\x04\x00\x00\x00', re.S)
STATE_SETUP = re.compile(rb'\x84\xc0\x74.\x41\xc7\x46\x08\x04\x00\x00\x00', re.S)
# addImageProtocolConformanceBlockCallbackUnsafe: test al,al; je; add rbx,r14; lea rdi (register every section).
CONFORMANCE = re.compile(rb'\x84\xc0\x74.\x4c\x01\xf3\x48\x8d\x3d', re.S)
CONFORMANCE_TABLES = ('type', 'metadata', 'foreign')


def open_family(main):
    with open(main, 'rb') as f:
        head = f.read(0x1000)
    subcaches = struct.unpack_from('<I', head, 396)[0]
    images = struct.unpack_from('<I', head, 452)[0]
    return CandidateFamily(main, image_count=images, subcache_count=subcaches)


def file_offset(family, address):
    for index, f in enumerate(family.files):
        for m in f.mappings:
            if m.address <= address and address + 8 <= m.address + m.size:
                return index, m.offset + address - m.address
    raise ValueError(f'unmapped address {address:#x}')


def sections(refs, path, name):
    rows = [r for r in refs.ranges if r[2] == (path, '__TEXT', name)]
    require(len(rows) == 1, f'{path} {name}: expected one section')
    return rows[0][0], rows[0][1]


def override_sites(family, refs, conformance=False):
    """[(id, address, bytes before)] for the two switch-off calls (and the conformance call), or a refusal."""
    target = cached_symbols(family, LIBDYLD).get('_' + OVERRIDDEN) or cached_symbols(family, LIBDYLD).get(OVERRIDDEN)
    require(target, f'{OVERRIDDEN} not exported by libdyld')
    slides = family_slides(family)
    lo, hi = sections(refs, SWIFT_CORE, '__stubs')
    stubs = []
    blob = bytes(family.read(lo, hi - lo))
    for m in re.finditer(rb'\xff\x25', blob):
        at = lo + m.start()
        got = at + 6 + struct.unpack_from('<i', blob, m.start() + 2)[0]
        try:
            pointed = pointer_at(slides, got)[3]['target']
        except Exception:
            continue
        if pointed == target:
            stubs.append(at)
    require(len(stubs) == 1, f'libswiftCore: expected one {OVERRIDDEN} stub, found {len(stubs)}')
    lo, hi = sections(refs, SWIFT_CORE, '__text')
    text = bytes(family.read(lo, hi - lo))
    found = {}
    for m in re.finditer(rb'\xe8', text):
        i = m.start()
        if i + 5 > len(text) or lo + i + 5 + struct.unpack_from('<i', text, i + 1)[0] != stubs[0]:
            continue
        tail = text[i + 5:i + 5 + 32]
        for name, pattern in (('image-loaded', IMAGE_LOADED), ('state-setup', STATE_SETUP), ('conformance', CONFORMANCE)):
            if pattern.match(tail):
                require(name not in found, f'{name}: more than one call site')
                found[name] = (lo + i, text[i:i + 5])
    wanted = {'image-loaded', 'state-setup'} | ({'conformance'} if conformance else set())
    require(wanted <= set(found), f'libswiftCore switch-off sites not found: {sorted(wanted - set(found))}')
    return [(name, address, before) for name, (address, before) in sorted(found.items()) if name in wanted]


def conformance_edits(family, owner, over, add):
    """Sentinel the dyld conformance buckets that must not answer; return counts and the gate's findings."""
    base, roots = swift_roots(family)
    counts, mixed, lost = {}, [], []
    for kind in CONFORMANCE_TABLES:
        address = base + roots[kind]
        table = read_table(family.read, address, kind)
        head = table.header
        targets = 1056 + head.rounded_tab_size + head.capacity
        bucket = {}
        for i, value in enumerate(struct.unpack_from(f'<{head.capacity}I', table.data, targets)):
            if value != head.sentinel:
                bucket[value] = i
        rows = {r[0]: r for r in table.rows}
        removed_groups = removed_rows = 0
        for group in table.groups:
            members = [rows[p] for p in group]
            _, a, b, _ = members[0]
            key_images = {owner(base + b)} | (set() if kind == 'foreign' else {owner(base + a)})
            conf_images = [owner(base + ((raw >> 1) & ((1 << 47) - 1))) for _, _, _, raw in members]
            key_hit = bool(key_images & over)
            conf_hit = [image in over for image in conf_images]
            if key_hit:
                # A conformance declared in an ordinary cache image on an overridden type or protocol. Today that image's
                # section is scanned and its references resolve to the disk copy; with the call false it is not scanned,
                # and dyld's row (keyed by the cache copy) cannot answer: the conformance would be lost.
                lost += [dict(table=kind, key_images=sorted(key_images & over), conformance_image=image)
                         for image, hit in zip(conf_images, conf_hit) if not hit]
            if not key_hit and not any(conf_hit):
                continue
            if not key_hit and not all(conf_hit):
                mixed.append(dict(table=kind, rows=len(members), overridden=sum(conf_hit),
                                  images=sorted({i for i, h in zip(conf_images, conf_hit) if h})))
                continue
            index = bucket[group[0]]
            at = address + targets + index * 4
            add(at, struct.pack('<I', group[0]), struct.pack('<I', head.sentinel), f'conformance-{kind}-overridden',
                f'{kind} conformance key or conformance in an overridden image ({sorted((key_images | set(conf_images)) & over)}): '
                'the bucket is the sentinel, so dyld reports not found and the runtime scans the disk copy')
            removed_groups += 1
            removed_rows += len(members)
        if removed_groups:
            raw = bytes(family.read(address + 4, 4))
            occupied = struct.unpack('<I', raw)[0]
            add(address + 4, raw, struct.pack('<I', occupied - removed_groups), f'conformance-{kind}-occupied',
                'occupied count of the same table after the removed buckets (dyld does not read it; readers do)')
        counts[kind] = dict(groups=removed_groups, rows=removed_rows)
    require(not lost, 'gate (conformance): conformances in cache images on overridden types or protocols would be lost: '
            + json.dumps(sorted({(r['table'], r['conformance_image'], tuple(r['key_images'])) for r in lost})) + f' ({len(lost)} rows)')
    return counts, mixed


def owner_lookup(refs):
    def owner(address):
        i = bisect.bisect_right(refs.starts, address) - 1
        return refs.ranges[i][2][0] if i >= 0 and address < refs.ranges[i][1] else None
    return owner


def catalog_keys(path):
    """Static pointer keys (descriptor;arg,...) from research's name-list TSV."""
    keys = []
    for line in Path(path).read_text().splitlines():
        cols = line.split('\t')
        if line.startswith('#') or cols[0] == 'name' or len(cols) < 6 or cols[5] in ('', '-'):
            continue
        descriptor, _, arguments = cols[5].partition(';')
        keys.append((cols[0], [int(descriptor, 16)] + [int(a, 16) for a in arguments.split(',') if a]))
    return keys


def plan(main, overridden, catalog=None, conformance=False):
    family = open_family(main)
    try:
        refs = References(family)
        owner = owner_lookup(refs)
        paths = {p for p, _ in family.images}
        overridden = sorted(set(overridden))
        unknown = [p for p in overridden if p not in paths]
        require(not unknown, f'overridden paths are not cache images: {unknown}')
        over = set(overridden)
        entries, roots = pointer_entries(family)
        base = roots['base']
        blocked = []
        for key, values in entries.items():
            hit = {owner(base + k) for k in key} | {owner(base + v) for v in values}
            if hit & over:
                blocked.append(dict(kind='apple-entry', key=[hex(base + k) for k in key], images=sorted(hit & over)))
        for name, key in (catalog_keys(catalog) if catalog else []):
            hit = {owner(a) for a in key}
            if hit & over:
                blocked.append(dict(kind='catalog-key', name=name, images=sorted(hit & over)))
        require(not blocked, 'gate: prespecialized entries reference overridden images: '
                + json.dumps(blocked[:5]) + (f' (+{len(blocked) - 5})' if len(blocked) > 5 else ''))
        edits = {}
        def add(address, before, after, edit_id, reason):
            index, offset = file_offset(family, address)
            edits.setdefault(index, []).append(dict(id=edit_id, at=offset, before=before.hex(), after=after.hex(),
                                                    address=hex(address), reason=reason))
        for name, address, before in override_sites(family, refs, conformance):
            add(address, before, NOP_CALL, f'prespecialization-{name}',
                f'{OVERRIDDEN}() no longer turns prespecialized metadata off ({name}); the gate checked that no '
                'pointer-keyed entry references an overridden image' if name != 'conformance' else
                f'{OVERRIDDEN}() no longer makes every cache image register its conformance sections; dyld\'s '
                'tables answer for cache images after the overridden keys were removed')
        conformances, mixed = conformance_edits(family, owner, over, add) if conformance else (None, [])
        base2, sroots = swift_roots(family)
        reader = FamilyReader(family)
        order = [p for p, _ in family.images]
        slides = family_slides(family)
        dropped = {}
        for (slot, target, _), _count in sorted(descriptors(family, reader, base2, sroots, order).items()):
            image = owner(target)
            if image not in over:
                continue
            data = base2 + sroots['prespecialization']
            descriptor_map = reader.ptr(data + 40)
            address = descriptor_map + 8 + slot * 8
            file, mapping, slide, pointer = pointer_at(slides, address)
            require(pointer['target'] == target, 'descriptor slot decode differs')
            raw = slide.retarget(address - mapping.address, target, 0)
            add(address, struct.pack('<Q', pointer['raw']), struct.pack('<Q', raw), 'descriptor-map-overridden',
                f'descriptor in overridden {image}: the slot reads as empty, so the name takes the normal runtime path')
            dropped[image] = dropped.get(image, 0) + 1
        names = ['main'] + [f'.{i:02d}' for i in range(1, len(family.files))]
        files = []
        for index, rows in sorted(edits.items()):
            path = family.files[index].path
            files.append(dict(file=names[index], path=str(path), sha256=sha256(path), edits=rows))
        return dict(schema=1, kind='prespecialization-overrides', cache_uuid=family.main.data[88:104].hex().upper(),
                    overridden=overridden, apple_entries=len(entries), catalog_keys=len(catalog_keys(catalog)) if catalog else 0,
                    descriptor_slots_dropped=dropped,
                    conformance_site=('false; overridden keys removed' if conformance else 'unchanged (dyld conformance '
                                      'table is invalid for an overridden image with __swift5_proto)'),
                    conformance_removed=conformances, conformance_kept_mixed=mixed, files=files)
    finally:
        family.close()


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 24), b''):
            h.update(chunk)
    return h.hexdigest()


def code_directory_identifier(data):
    offset, size = struct.unpack_from('<QQ', data, 40)
    blob = data[offset:offset + size]
    magic, _, count = struct.unpack_from('>III', blob, 0)
    require(magic == 0xfade0cc0, 'unknown signature SuperBlob')
    for k in range(count):
        kind, start = struct.unpack_from('>II', blob, 12 + 8 * k)
        if kind == 0:
            ident = struct.unpack_from('>I', blob, start + 20)[0]
            return bytes(blob[start + ident:blob.index(b'\0', start + ident)]).decode('ascii')
    raise ValueError('no CodeDirectory')


def apply(recipe, output):
    """Edited copies of the recipe's files, re-signed ad hoc with 4 KiB pages."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    written = []
    for row in recipe['files']:
        source = Path(row['path'])
        require(sha256(source) == row['sha256'], f'{source}: input differs from the recipe')
        data = bytearray(source.read_bytes())
        for edit in row['edits']:
            before, after = bytes.fromhex(edit['before']), bytes.fromhex(edit['after'])
            require(data[edit['at']:edit['at'] + len(before)] == before, f"{edit['id']} at {edit['at']:#x}: bytes differ")
            data[edit['at']:edit['at'] + len(after)] = after
        identifier = code_directory_identifier(data)
        limit = struct.unpack_from('<Q', data, 40)[0]
        mapping = struct.unpack_from('<I', data, 16)[0]
        text_size = struct.unpack_from('<Q', data, mapping + 8)[0]
        signature = emit_signature(data, limit, identifier, text_size)       # sizes the blob
        del data[limit:]
        struct.pack_into('<Q', data, 48, len(signature))
        data += emit_signature(data, limit, identifier, text_size)
        target = output / source.name
        target.write_bytes(data)
        written.append(dict(file=row['file'], path=str(target), sha256=sha256(target)))
    return written


def main(argv):
    ap = argparse.ArgumentParser(prog='make prespecialization-overrides', description=__doc__.split('\n\n')[0])
    ap.add_argument('cache', type=Path, help='main x86_64 cache file of the target (the installed one on m27)')
    ap.add_argument('--overridden', type=Path, required=True,
                    help='cache install paths present on disk on the target, one per line')
    ap.add_argument('--catalog-keys', type=Path, help="research's name-list TSV; its static keys go through the gate")
    ap.add_argument('--output', type=Path, required=True, help='recipe JSON to write')
    ap.add_argument('--conformance', action='store_true',
                    help='also make the conformance call false, after removing overridden keys from dyld\'s tables (V2)')
    ap.add_argument('--apply', type=Path, help='new directory for edited, re-signed copies (live swap)')
    args = ap.parse_args(argv)
    overridden = [l.strip() for l in args.overridden.read_text().splitlines() if l.strip() and not l.startswith('#')]
    recipe = plan(args.cache, overridden, args.catalog_keys, args.conformance)
    args.output.write_text(json.dumps(recipe, indent=1) + '\n')
    print(f"{args.output}: {sum(len(f['edits']) for f in recipe['files'])} edits in {len(recipe['files'])} files; "
          f"descriptor slots dropped {recipe['descriptor_slots_dropped']}"
          + (f"; conformance removed {recipe['conformance_removed']}, mixed kept {len(recipe['conformance_kept_mixed'])}"
             if args.conformance else ''))
    if args.apply:
        recipe['outputs'] = apply(recipe, args.apply)
        args.output.write_text(json.dumps(recipe, indent=1) + '\n')
        for row in recipe['outputs']:
            print(f"{row['path']} {row['sha256']}")
