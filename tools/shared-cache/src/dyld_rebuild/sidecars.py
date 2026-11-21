"""Derive complete external cache sidecars without changing cache bytes.

The original atlas/map are a calibration oracle, not a template to mutate.
Only the two proven late libobjc synthetic segments may be excluded.
"""
from pathlib import Path
from bisect import bisect_right
import hashlib
import json
import struct
import uuid
from .atlas import BASE
from .cache import commands
from .exports import require


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify_manifest(path, root):
    """Verify every manifest byte before opening the family; accept fresh reports."""
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    if 'files' in value and 'main' in value:
        rows = [(root / r['path'], r) for r in value['files']]
        main = root / value['main']
        images = value.get('images', 3608)
    else:
        require(value.get('schema') == 1 and 'outputs' in value and 'original_files' in value,
                'unknown sidecar input manifest')
        rows = [(path.parent / r['file'], r) for r in value['original_files'] + value['outputs']]
        main = path.parent / BASE
        images = value['images']
    require(rows and len({p.resolve() for p, _ in rows}) == len(rows), 'duplicate input manifest path')
    pins = []
    for file, record in rows:
        require(file.is_file() and not file.is_symlink() and file.stat().st_size == record['size']
                and sha(file) == record['sha256'], 'sidecar input byte drift: ' + str(file))
        pins.append(dict(path=str(file.resolve()), size=record['size'], sha256=record['sha256']))
    names = {p.name for p, _ in rows}
    cache_names = {n for n in names if n == BASE or (n.startswith(BASE + '.') and n[len(BASE)+1:].isdigit())}
    require(len(cache_names) > 1 and cache_names == {BASE} | {f'{BASE}.{i:02d}' for i in range(1, len(cache_names))},
            'missing/noncontiguous cache manifest files')
    require(main.name == BASE and main in [p for p, _ in rows], 'missing main cache pin')
    return main, images, len(cache_names)-1, pins


def segment_name(raw):
    """Factory name() and snprintf/PropertyList String consume a C string."""
    require(len(raw) == 16 and b'\0' in raw, 'segment name lacks bounded C-string terminator')
    name = raw.split(b'\0', 1)[0]
    require(name and all(32 <= c < 127 for c in name), 'unsupported segment C string')
    return name.decode('ascii')


def images(family):
    result = []
    for path, address in family.images:
        segments, identities = [], []
        for command_address, kind, raw in commands(family, address):
            if kind == 0x1b:
                require(len(raw) == 24, 'invalid image UUID command')
                identities.append(bytes(raw[8:24]))
            elif kind == 0x19:
                require(len(raw) >= 72, 'short image segment command')
                vm, size, offset, file_size, maximum, initial, sections, flags = struct.unpack_from('<4Q4I', raw, 24)
                require(len(raw) == 72 + sections*80 and file_size <= size and initial & ~maximum == 0,
                        'invalid image segment geometry')
                name = segment_name(raw[8:24])
                # Empty segment ranges are valid; positive file spans must locate
                # in exactly the file/offset claimed by the Mach-O command.
                if file_size:
                    _, located = family.locate(vm, file_size)
                    require(located == offset, 'segment file/VM offset mismatch')
                if size:
                    family.locate(vm, size)
                segments.append(dict(name=name, padr=vm, size=size, foff=offset, fsze=file_size,
                    # 27 (568-byte headers) records each segment's maximum protection (every atlas segment of
                    # 26B131); 26's atlas fits the name rule.
                    perm=maximum if family.main.mapping_offset == 568 else 5 if name == '__TEXT' else 1 if name == '__LINKEDIT' else 3,
                    maximum=maximum, initial=initial, sections=sections, flags=flags,
                    raw_name=bytes(raw[8:24]).hex(), command=command_address))
        require(len(identities) == 1 and segments, 'missing/duplicate image UUID or segments')
        result.append(dict(segs=segments, name=path, padr=address, uuid=identities[0]))
    return result


def clean_segment(segment):
    return {key: segment[key] for key in ('name', 'padr', 'size', 'foff', 'fsze', 'perm')}


def ordered_mapping_runs(files):
    """Partition physical file descriptors into globally ordered mapping runs.

    SharedCacheMapper flattens dscs/maps without sorting, and MemoryMap performs
    binary search. Interleaved file VM ranges therefore need repeated file
    descriptors. Every repeated descriptor retains the physical file identity,
    base and extent; only its mapping subset changes. Public filePaths/subCaches
    consequently enumerate descriptors and can repeat a physical file.
    """
    require(files and len({f['name'] for f in files}) == len(files),
            'duplicate/missing physical atlas file')
    flat = []
    for index, file in enumerate(files):
        require(file['maps'], 'physical atlas file has no mappings')
        for mapping in file['maps']:
            require(mapping['size'] > 0 and mapping['padr'] >= file['padr']
                    and mapping['padr'] + mapping['size'] <= file['padr'] + file['size'],
                    'atlas mapping outside physical file VM extent')
            flat.append((mapping['padr'], index, mapping))
    flat.sort(key=lambda row: row[0])
    require(all(a[0] + a[2]['size'] <= b[0] for a, b in zip(flat, flat[1:])),
            'atlas mappings overlap')
    runs, owners = [], []
    for _, index, mapping in flat:
        if not owners or owners[-1] != index:
            runs.append({**files[index], 'maps': []})
            owners.append(index)
        runs[-1]['maps'].append(dict(mapping))
    # Prove an exact partition, including physical ownership and file offsets.
    for index, original in enumerate(files):
        parts = [run for owner, run in zip(owners, runs) if owner == index]
        require(all({k: v for k, v in run.items() if k != 'maps'} ==
                    {k: v for k, v in original.items() if k != 'maps'} for run in parts),
                'atlas fragment physical identity differs')
        require([m for run in parts for m in run['maps']] == original['maps'],
                'atlas fragments do not partition physical mappings in order')
    return runs, dict(physical_files=len(files), mapping_descriptors=len(runs),
                      mapping_records=len(flat), repeated_file_descriptors=len(runs)-len(files),
                      public_file_paths_may_repeat=len(runs) != len(files))


def verify_mapping_coverage(cache):
    """Check the flattened binary-search contract and segment backing offsets."""
    rows = [(m, file) for file in cache['dscs'] for m in file['maps']]
    require(rows and all(a[0]['padr'] + a[0]['size'] <= b[0]['padr']
                         for a, b in zip(rows, rows[1:])),
            'flattened atlas mappings are not ordered/nonoverlapping')
    starts = [m['padr'] for m, _ in rows]
    checked = 0
    for image in cache['imgs']:
        for segment in image['segs']:
            if not segment['size']:
                continue
            index = bisect_right(starts, segment['padr']) - 1
            require(index >= 0, 'atlas segment precedes mappings')
            mapping, file = rows[index]
            require(segment['padr'] + segment['size'] <= mapping['padr'] + mapping['size'],
                    'atlas segment is not covered by one mapping')
            if segment['fsze']:
                require(segment['foff'] == mapping['foff'] + segment['padr'] - mapping['padr'],
                        'atlas segment backing file offset differs')
            # SharedCacheMapper uses this expression for same-cache EX bytes.
            if mapping['prot'] == 5:
                require(cache['padr'] + file['voff'] + mapping['foff'] == mapping['padr'],
                        'atlas executable local-memory address differs')
            checked += 1
    return dict(images=len(cache['imgs']), segments=checked, flattened_mappings_ordered=True)


def calibrate(family, atlas_value, original_map):
    """Prove every original value and text byte; produce bounded exclusion rules."""
    original = atlas_value['names'][BASE]
    decoded = images(family)
    require(len(decoded) == len(original['imgs']), 'original atlas image count differs')
    exclusions = []
    for actual, expected in zip(decoded, original['imgs']):
        require({k: actual[k] for k in ('name', 'padr', 'uuid')} ==
                {k: expected[k] for k in ('name', 'padr', 'uuid')}, 'original image atlas identity differs')
        remaining = list(expected['segs'])
        for seg in actual['segs']:
            synthetic = (actual['name'] == '/usr/lib/libobjc.A.dylib' and seg['name'] in ('__OBJC_RO', '__OBJC_RW'))
            if remaining and clean_segment(seg) == remaining[0] and not (synthetic and family.main.mapping_offset == 568):
                remaining.pop(0)
            else:
                if synthetic and remaining and clean_segment(seg) == remaining[0]:
                    remaining.pop(0)
                require(actual['name'] == '/usr/lib/libobjc.A.dylib' and seg['name'] in ('__OBJC_RO', '__OBJC_RW')
                        and seg['sections'] == seg['flags'] == 0 and seg['size'] == seg['fsze']
                        and seg['maximum'] == seg['initial'] == (1 if seg['name'] == '__OBJC_RO' else 3),
                        'unexplained original atlas segment omission')
                exclusions.append(dict(image=actual['name'], **{k:seg[k] for k in
                    ('name','padr','size','fsze','maximum','initial','sections','flags','raw_name')}))
        require(not remaining, 'original atlas contains unmatched segments')
    # libobjc's __OBJC_RO/__OBJC_RW: 26 leaves them out of atlas and map; 27 lists them in the atlas, not in the map.
    require(len(exclusions) == 2 and {r['name'] for r in exclusions} == {'__OBJC_RO','__OBJC_RW'},
            'unexpected synthetic exclusion inventory')
    value, text, _ = derive(family, exclusions)
    # A source family re-signed in place (m27 images: 4 KiB code pages) keeps Apple's atlas while its files grow. Only the
    # signature may differ: Apple's recorded size must lie past this file's (unchanged) signature start.
    signature_starts = {f.path.name: f.signature_offset for f in family.files}
    for dsc, recorded in zip(value['names'][BASE]['dscs'], atlas_value['names'][BASE]['dscs']):
        if dsc['fsze'] != recorded['fsze']:
            require(dsc['name'] == recorded['name'] and signature_starts[dsc['name']] < recorded['fsze'],
                    'original atlas file size differs outside the code signature')
            dsc['fsze'] = recorded['fsze']
    require(value == atlas_value, 'original full atlas logical values differ')
    require(text == original_map, 'original full map text differs')
    return exclusions


def derive(family, exclusions):
    require(len(exclusions) == 2, 'missing calibrated exclusion contract')
    map_only = family.main.mapping_offset == 568
    main = family.main
    start, region_size = struct.unpack_from('<QQ', main.data, 224)
    require(start == main.mappings[0].address and region_size > 0, 'shared region header differs')
    require(not any(main.data[400:416]), 'symbol sidecar contract not supported')
    cache_uuid = bytes(main.data[88:104])
    cache = dict(uuid=cache_uuid, padr=start, size=region_size, dscs=[], psze=4, imgs=[])
    if map_only:
        # 27: cput = cputype << 32 | cpusubtype (26B131: 0x0100000700000003 for the cache and every image).
        cpu, sub = struct.unpack_from('<II', main.data, 552)
        cache['cput'] = cpu << 32 | sub
        cache['psze'] = 8   # as in 26B131's atlas (26: 4)
    lines, omitted, anomalies = [], [], []
    for file in family.files:
        first = file.mappings[0].address
        end = max(m.address + m.size for m in file.mappings)
        require(start <= first < end <= start + region_size, 'mapping outside shared region')
        maps = []
        for mapping in file.mappings:
            require(mapping.maximum in (1,3,5), 'unsupported map protection')
            maps.append(dict(size=mapping.size, padr=mapping.address, foff=mapping.offset, prot=mapping.maximum))
            prot = {1:'RO',3:'RW',5:'EX'}[mapping.maximum]
            divisor, unit = (1024*1024,'MB') if mapping.size > 1024*1024 else (1024,'KB')
            lines.append(f'mapping  {prot} {mapping.size//divisor:4d}{unit} 0x{mapping.address:X} -> 0x{mapping.address+mapping.size:X}\n')
        cache['dscs'].append(dict(name=file.path.name, uuid=bytes(file.data[88:104]), voff=first-start,
            fsze=len(file.data), padr=first, maps=maps, size=end-first))
    lines.append('\n\n')
    for image in images(family):
        retained = []
        for segment in image['segs']:
            rules = [r for r in exclusions if r['image'] == image['name'] and r['name'] == segment['name']]
            if rules:
                require(len(rules) == 1 and all(segment[k] == v for k,v in rules[0].items() if k!='image'),
                        'calibrated synthetic segment changed')
                omitted.append(dict(image=image['name'], name=segment['name']))
                if map_only:
                    retained.append(dict(clean_segment(segment), map=False))
                continue
            if bytes.fromhex(segment['raw_name']).split(b'\0',1)[-1].strip(b'\0') and b'\0' in bytes.fromhex(segment['raw_name']):
                anomalies.append(dict(image=image['name'], raw_name=segment['raw_name'], factory_name=segment['name']))
            retained.append(clean_segment(segment))
        image['segs'] = [{k: v for k, v in s.items() if k != 'map'} for s in retained]
        if map_only:
            cpu, sub = struct.unpack('<II', family.read(image['padr'] + 4, 8))
            image['cput'] = cpu << 32 | sub
        cache['imgs'].append(image)
        lines.append(image['name']+'\n')
        for seg in (s for s in retained if s.get('map', True)):
            lines.append(f"\t{seg['name']:>16} 0x{seg['padr']:08X} -> 0x{seg['padr']+seg['size']:08X}\n")
        lines.append('\n')
    require(len(omitted) == len(exclusions), 'calibrated synthetic segment missing')
    cache['dscs'], mapping_runs = ordered_mapping_runs(cache['dscs'])
    coverage = verify_mapping_coverage(cache)
    identity = str(uuid.UUID(bytes=cache_uuid)).upper()
    return dict(names={BASE:cache}, uuids={identity:cache}), ''.join(lines), dict(
        exclusions=omitted, embedded_name_suffixes=anomalies, mapping_runs=mapping_runs,
        mapping_coverage=coverage)
