#!/usr/bin/env python3
"""Assemble checked components into a fresh cache family for offline evaluation.

This stage refuses conflicting edits and re-signs changed files. It never marks
an image complete: required program loaders, sidecars and image sealing are
separate, explicit gates.
"""
import argparse
import hashlib
import json
import mmap
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dyld_rebuild.assemble import checked_byte_edits, pointer_pages
from dyld_rebuild.candidate import CandidateFamily
from dyld_rebuild.cache_exports import path_trie
from dyld_rebuild.codesign import geometry, inspect
from dyld_rebuild.emit_cache import image_records, sign_with_uuid, write_image_lists
from dyld_rebuild.exports import require
from dyld_rebuild.objc_emit import check_small_method_types
from dyld_rebuild.loader_generation import cache_manifest, sha
from dyld_rebuild.graph_contract import checked_manifest
from dyld_rebuild.metadata import load as load_layout, slot_file_offset
from dyld_rebuild.slides import family_slides, SlideInfoV2


def digest(data): return hashlib.sha256(data).hexdigest()


def read_blob(row):
    path = (ROOT / row['path']).resolve()
    data = path.read_bytes()
    require(len(data) == row['size'] and digest(data) == row['sha256'], 'component blob hash/size drift')
    return data


def fresh_metadata(family, layout, records, blobs, fields):
    rows = layout['mappings']
    limit = rows[-1]['file_offset'] + rows[-1]['size']
    identifier = inspect(family.main.data, verify_pages=False)['identifier']
    size = geometry(limit, identifier)[-1]
    data = bytearray(limit + size)
    data[:16] = b'dyld_v1  x86_64\0'
    for offset, length in ((104, 8), (216, 8), (364, 12), (456, 4)):
        data[offset:offset + length] = family.main.data[offset:offset + length]
    header = family.main.mapping_offset
    # 27 (568): carry the source's cputype/cpusubtype words after prewarmingDataSize unchanged.
    data[552:header] = family.main.data[552:header]
    struct.pack_into('<II', data, 16, header, len(rows))
    struct.pack_into('<QQ', data, 40, limit, size)
    struct.pack_into('<Q', data, 224, rows[0]['address'])
    slide_table = header + 32 * len(rows)
    struct.pack_into('<II', data, 312, slide_table, len(rows))
    for i, row in enumerate(rows):
        struct.pack_into('<3Q2I', data, header + 32 * i, row['address'], row['size'],
                         row['file_offset'], row['maximum'], row['initial'])
        struct.pack_into('<6Q2I', data, slide_table + 56 * i, row['address'], row['size'],
                         row['file_offset'], 0, 0, row['flags'], row['maximum'], row['initial'])
    images = slide_table + 56 * len(rows)
    text = images + len(records) * 32
    strings = text + len(records) * 32
    write_image_lists(data, records, images, text, strings, layout['prefix_size'])
    struct.pack_into('<II', data, 392, strings, 0)
    struct.pack_into('<II', data, 512, strings, 0)
    intervals = []
    for row, value in blobs:
        slot = layout[row['reservation']]
        address = row['address']
        require(slot['address'] <= address and address + len(value) <= slot['address'] + slot['reserve'],
                'metadata blob escapes named reservation')
        offset = slot_file_offset(layout, row['reservation'], address - slot['address'] + len(value))
        offset += address - slot['address']
        intervals.append((offset, offset + len(value)))
        data[offset:offset + len(value)] = value
    intervals.sort()
    require(all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:])), 'metadata blobs overlap')
    # Canonical ObjC objects and generated Swift metadata share one slide graph.
    rw = rows[1]
    starts = [0x4000] * (rw['size'] // 4096)
    pages = {}
    targets = {}
    for row in fields:
        address, target = row['address'], row['target']
        # Swift enum layout programs pack pointers on four-byte boundaries;
        # v2 slide chains encode that alignment, but a pointer must fit one page.
        require(rw['address'] <= address <= rw['address'] + rw['size'] - 8
                and address % 4 == 0 and address % 4096 <= 4088,
                'new metadata pointer outside writable mapping or slide page')
        require(address not in targets and target > 0, 'duplicate/null metadata pointer')
        targets[address] = target
        pages.setdefault((address - rw['address']) // 4096, []).append(address)
    base = family.main.mappings[0].address
    mask = 0x00ffff0000000000
    for page, addresses in pages.items():
        addresses.sort()
        starts[page] = (addresses[0] - rw['address'] - page * 4096) // 4
        for i, address in enumerate(addresses):
            offset = rw['file_offset'] + address - rw['address']
            require(data[offset:offset + 8] == bytes(8), 'new pointer field is not zero initialized')
            value = targets[address] - base
            require(0 < value < 2**64 and not value & mask, 'new metadata target not representable')
            delta = addresses[i + 1] - address if i + 1 < len(addresses) else 0
            require(delta == 0 or delta >= 8, 'new metadata pointer overlap')
            struct.pack_into('<Q', data, offset, value | (delta << 38))
    block = struct.pack('<6I2Q', 2, 4096, 40, len(starts), 0, 0, mask, base)
    block += struct.pack(f'<{len(starts)}H', *starts)
    slideoff = slot_file_offset(layout, 'slide_info', len(block))
    data[slideoff:slideoff + len(block)] = block
    struct.pack_into('<QQ', data, slide_table + 56 + 24, slideoff, len(block))
    reader = SlideInfoV2(block, data, rw['file_offset'], rw['size'])
    decoded = {rw['address'] + offset: reader.decode(raw)
               for page in range(len(starts)) for offset, raw in reader.page(page).items()}
    require(decoded == targets, 'new metadata slide roundtrip differs')
    signature = sign_with_uuid(data, identifier, rows[0]['size'])
    return data, signature


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--structural-manifest', type=Path, required=True)
    ap.add_argument('--plan', type=Path, required=True)
    ap.add_argument('--allocation', type=Path, default=ROOT / 'config/metadata-layout.json')
    ap.add_argument('--provider-objc', type=Path, required=True)
    ap.add_argument('--global-objc', type=Path, required=True)
    ap.add_argument('--patch-report', type=Path, required=True)
    ap.add_argument('--lookup-report', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--swift', type=Path)
    args = ap.parse_args()
    output = args.output.resolve()
    require(not output.exists(), 'output must be fresh scratch')
    main_path, count, image_count, pins = cache_manifest(args.structural_manifest)
    family = CandidateFamily(main_path, image_count=image_count, subcache_count=count - 1)
    streams, maps = [], {}
    try:
        layout = load_layout(args.allocation, family, args.plan)
        placement_plan = json.loads(args.plan.read_text())
        graph=checked_manifest(args.structural_manifest,root=ROOT,images=image_count,files=count,stage='structural',family=family,plan_path=args.plan)
        provider = json.loads(args.provider_objc.read_text())
        global_ = json.loads(args.global_objc.read_text())
        patches = json.loads(args.patch_report.read_text())
        lookups = json.loads(args.lookup_report.read_text())
        require(provider['inputs']['base_report_sha256'] == sha(args.structural_manifest) and
                provider['inputs']['allocation_sha256'] == sha(args.allocation), 'provider recipe input differs')
        require(patches['provider_plan_sha256'] == sha(args.plan) and patches['allocation_sha256'] == sha(args.allocation),
                'patch graph inputs differ')
        require(global_['schema'] == provider['schema'] == 1, 'unknown component recipe schema')
        require(global_['mode'] == 'global_objc_bootstrap' and not global_['final_publication_allowed'],
                'this assembler stage requires explicit bootstrap metadata')
        for record in global_['inputs'].values():
            require(sha(ROOT / record['path']) == record['sha256'], 'global recipe dependency drift')
        require(global_['inputs']['provider']['sha256'] == sha(args.provider_objc) and
                global_['inputs']['allocation']['sha256'] == sha(args.allocation),
                'global recipe provider/allocation differs')
        global_tables = json.loads((ROOT / global_['inputs']['tables']['path']).read_text())
        normalize = lambda rows: sorted((str((ROOT / r['path']).resolve()), r['size'], r['sha256']) for r in rows)
        require(normalize(global_tables['candidate_pins']) == normalize(pins) == normalize(patches['candidate_inputs']),
                'global/patch metadata belongs to a different structural family')
        blobs, byte_edits = [], []
        for row in global_['blobs']:
            blobs.append((row, read_blob(row)))
        for row in provider['blobs']:
            value = bytes.fromhex(row['data'])
            require(len(value) == row['size'] and digest(value) == row['sha256'], 'provider blob drift')
            blobs.append((row, value))
        for row in global_['file_edits']:
            before = family.read(row['address'], row['size'])
            require(digest(before) == row['expected_sha256'], 'in-place metadata source drift')
            byte_edits.append((row['address'], before, read_blob(row)))
        for row in provider['byte_edits'] + global_['byte_edits']:
            byte_edits.append((row['address'], bytes.fromhex(row['before']), bytes.fromhex(row['after'])))
        pointer_edits = list(provider['pointer_edits']) + list(global_['pointer_edits'])
        new_pointer_fields = list(provider['new_pointer_fields'])
        swift = json.loads(args.swift.read_text()) if args.swift else None
        if swift:
            for row in swift['blobs']:
                blobs.append((row, bytes.fromhex(row['data'])))
            new_pointer_fields.extend(swift['new_pointer_fields'])
            pointer_edits.extend(swift['pointer_edits'])
            byte_edits.extend((row['address'], bytes.fromhex(row['before']), bytes.fromhex(row['after']))
                              for row in swift['byte_edits'])
        pointer_rows = json.loads((args.patch_report.parent / 'pointer-edits.json').read_text())
        for row in pointer_rows:
            pointer_edits.append(dict(address=row['location'], expected=row['expected_target'], target=row['target_value'],
                                      member_before=True, member_after=row['encoding'] != 'fixed_unslid_literal'))
        page_plans = pointer_pages(family_slides(family), pointer_edits)
        for file, mapping, slide, page in page_plans:
            for offset, before, after in page['writes']:
                byte_edits.append((mapping.address + offset, before, after))
        checked = checked_byte_edits(byte_edits)
        for address, before, after in checked:
            require(family.read(address, len(before)) == before, 'byte edit source mismatch')
        patch_value = (args.patch_report.parent / 'patch-table.bin').read_bytes()
        trie_value = (args.lookup_report.parent / 'path-trie.bin').read_bytes()
        require(digest(patch_value) == patches['patch_table']['sha256'] and
                len(patch_value) == patches['patch_table']['size'], 'patch-table bytes drift')
        require(digest(trie_value) == lookups['path_trie']['sha256'] and
                len(trie_value) == lookups['path_trie']['size'], 'path-trie bytes drift')
        original_trie, original_trie_size = struct.unpack_from('<QQ', family.main.data, 264)
        expected_aliases = path_trie(family.read(original_trie, original_trie_size), image_count)
        for index, (path, _) in enumerate(family.images):
            require(path not in expected_aliases or expected_aliases[path] == index,
                    'structural path alias collides with final identity')
            expected_aliases[path] = index
        require(path_trie(trie_value, image_count) == expected_aliases,
                'final path trie does not preserve all aliases and canonical image indexes')
        for name, value in (('patch_table', patch_value), ('path_trie', trie_value)):
            blobs.append((dict(reservation=name, address=layout[name]['address']), value))
        records = image_records(family, [])
        meta, meta_signature = fresh_metadata(family, layout, records, blobs, new_pointer_fields)
        output.mkdir()
        for file in family.files:
            dest = output / file.path.name
            copied = subprocess.run(['/bin/cp', '-c', str(file.path), str(dest)], capture_output=True)
            if copied.returncode:
                shutil.copyfile(file.path, dest)
            stream = dest.open('r+b')
            streams.append(stream)
            maps[file.path.name] = mmap.mmap(stream.fileno(), 0)
        changed = set()
        for address, before, after in checked:
            file, offset = family.locate(address, len(before))
            maps[file.path.name][offset:offset + len(after)] = after
            changed.add(file.path.name)
        for file, mapping, slide, page in page_plans:
            if page['old_start'] == page['new_start']:
                continue
            index = file.mappings.index(mapping)
            infooff = struct.unpack_from('<Q', file.data, file.slide_offset + index * 56 + 24)[0]
            at = infooff + 40 + 2 * page['page']
            require(struct.unpack_from('<H', maps[file.path.name], at)[0] == page['old_start'], 'slide start source drift')
            struct.pack_into('<H', maps[file.path.name], at, page['new_start'])
            changed.add(file.path.name)
        metadata_name = main_path.name + layout['subcache_suffix']
        (output / metadata_name).write_bytes(meta)
        del meta
        # Rebuild the main subcache vector and move only the seven added paths
        # that immediately follow it. Original path strings keep their offsets.
        main = maps[main_path.name]
        suboff, old_count = struct.unpack_from('<II', main, 392)
        require(old_count == count - 1, 'structural subcache count differs')
        cursor = suboff + count * 56
        imageoff, _ = struct.unpack_from('<II', main, 448)
        textoff, _ = struct.unpack_from('<QQ', main, 136)
        plan = json.loads(args.plan.read_text())
        original_images = len(records) - len(plan['path_trie']['additions'])
        for index in range(original_images, len(records)):
            value = records[index]['path'].encode() + b'\0'
            require(cursor + len(value) <= plan['main_updates']['extension_end'], 'expanded subcache/path table overflow')
            main[cursor:cursor + len(value)] = value
            struct.pack_into('<I', main, imageoff + index * 32 + 24, cursor)
            struct.pack_into('<I', main, textoff + index * 32 + 28, cursor)
            cursor += len(value)
        struct.pack_into('<II', main, 392, suboff, count)
        struct.pack_into('<QQ', main, 152, layout['patch_table']['address'], len(patch_value))
        struct.pack_into('<QQ', main, 264, layout['path_trie']['address'], len(trie_value))
        changed.add(main_path.name)
        signatures = {}
        for file in family.files[1:]:
            if file.path.name in changed:
                data = maps[file.path.name]
                identifier = inspect(data, verify_pages=False)['identifier']
                data[88:104] = bytes(16)
                signatures[file.path.name] = sign_with_uuid(data, identifier, file.mappings[0].size)
        for index, file in enumerate(family.files[1:]):
            main[suboff + index * 56:suboff + index * 56 + 16] = maps[file.path.name][88:104]
        struct.pack_into('<16sQ32s', main, suboff + old_count * 56,
                         bytes.fromhex(meta_signature['uuid']), layout['mappings'][0]['address'] - family.main.mappings[0].address,
                         layout['subcache_suffix'].encode() + b'\0')
        identifier = inspect(main, verify_pages=False)['identifier']
        main[88:104] = bytes(16)
        signatures[main_path.name] = sign_with_uuid(main, identifier, family.main.mappings[0].size)
        original_files = []
        for file in family.files:
            data = maps[file.path.name]
            data.flush()
            original_files.append(dict(file=file.path.name, size=len(data), sha256=digest(data)))
        metadata_record = dict(file=metadata_name, size=(output / metadata_name).stat().st_size,
                               sha256=sha(output / metadata_name), signature=meta_signature)
        candidate = CandidateFamily(output / main_path.name, image_count=image_count, subcache_count=count)
        try:
            require(candidate.images == family.images, 'assembled image graph changed')
            for row, value in blobs:
                actual = bytearray(candidate.read(row['address'], len(value)))
                if row.get('reservation') in ('new_protocol', 'swift_metadata', 'swift_runtime_data'):
                    # Absolute fields are slide encoded; all other bytes still
                    # have to match the pinned source blob exactly.
                    for field in new_pointer_fields:
                        offset = field['address'] - row['address']
                        if 0 <= offset <= len(value) - 8:
                            require(value[offset:offset + 8] == bytes(8), 'new protocol pointer source is not zero')
                            actual[offset:offset + 8] = bytes(8)
                require(actual == value, 'assembled metadata blob differs')
            # Every provider small method list, read back as 27's libobjc reads it inside the cache: each method's
            # types must be the string it had before the rewrite.
            method_lists = [r for r in provider['byte_edits'] if r['reason'].startswith('canonical direct small methods')]
            mismatches = []
            for row in method_lists:
                mismatches += check_small_method_types(candidate.read, layout['selector_base'], row['address'],
                                                       bytes.fromhex(row['before']))
            require(not mismatches, 'method types read back differently: %d, first %r' % (len(mismatches), mismatches[:1]))
        finally:
            candidate.close()
        result = {'schema': 1, 'classification': 'assembled ObjC/Swift/patch/trie bootstrap; not a complete boot candidate',
                  'stage': 'metadata-bootstrap', 'complete_candidate': False, 'images': image_count,
                  'producer_plan_sha256': sha(args.plan), 'producer_plan': dict(path=str(args.plan.resolve()),sha256=sha(args.plan)),
                  'graph_contract': graph, 'original_files': original_files, 'outputs': [metadata_record],
                  'input_pins': pins, 'byte_edits': len(checked), 'pointer_pages': len(page_plans),
                  'decoded_pointer_requests': len(pointer_edits), 'new_metadata_pointers': len(new_pointer_fields),
                  'swift_generation': dict(records=len(swift['records']), original_keys=swift['original_keys'],
                                           total_keys=swift['total_keys']) if swift else None,
                  'blob_count': len(blobs), 'small_method_lists_read_back': len(method_lists), 'changed_files': sorted(changed), 'signatures': signatures,
                  'components': {key: {'path': str(path), 'sha256': sha(path)} for key, path in
                                 [('provider_objc', args.provider_objc), ('global_objc', args.global_objc),
                                  ('patch', args.patch_report), ('lookup', args.lookup_report), ('allocation', args.allocation)]},
                  'remaining_gates': ['Regenerate cached PBL and fill HeaderInfo PBL fields.',
                                      'Regenerate every required program set from final filesystem inputs.',
                                      'Update atlas/map, independent integrated checks, and image pipeline sealing.',
                                      'Direct native override-state probe and target acceptance without extra security relaxation.']}
        if args.swift:
            result['components']['swift'] = dict(path=str(args.swift.resolve()), sha256=sha(args.swift))
        (output / 'build-report.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: result[k] for k in ('classification', 'byte_edits', 'pointer_pages', 'new_metadata_pointers', 'changed_files')}))
    finally:
        for data in maps.values(): data.close()
        for stream in streams: stream.close()
        family.close()


if __name__ == '__main__':
    main()
