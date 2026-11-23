"""Current-run metadata allocation and provider ObjC preparation.

Reservations are derived from current inventories and source extents, then
checked against all actual VM mappings. Every emitted blob must still fit its
reservation. No historical target allocation or canonical object map is read.
"""
import json
from pathlib import Path
import struct

from .cache import commands
from .cache_exports import FinalGraph
from .candidate import CandidateFamily
from .canonical_inputs import extend_pool
from .exports import require
from .graph_contract import objc_inventory
from .loader_generation import cache_manifest
from .metadata import SLOTS, validate
from .objc_emit import emit_recipe, provider_inventory, retired_classes
from .provider_layout import align
from .run_inputs import pin
from .run_prepare import _json, _verified


def method_type_strings(inventory):
    """The distinct type strings of the providers' small methods, which the selector pool carries."""
    return sorted({m['types'] for r in inventory['method_lists'] if r['small'] for m in r['methods']})


def allocate_metadata(plan, baseline, inventory, transfer, *, program_pool_size, cached_size, patch_size, atlas_size,
                      swift_sizes=None, swift_code_anchor=None):
    """Return a fresh collision-checked slot layout using measured input sizes."""
    source_tables = baseline['tables']
    missing = {}
    for group,kind,required in (
        ('selectors','selector',set(inventory['selectors'])),
        ('classes','class',{r['name'] for r in inventory['classes']}),
        ('protocols','protocol',{r['name'] for r in inventory['protocols']})):
        missing[group] = sorted(required-{r['name'] for r in source_tables[kind]['entries']})
    # Existing checked emitter materializes one protocol object. Other scopes
    # need a generalized emitter; the actual inventory settles this check.
    require(len(missing['protocols']) == 1, 'checked provider emitter requires exactly one new canonical protocol')
    images = plan['final_image_paths']; count = len(images)
    prefix = align(plan.get('cache_header_size', 552)+3*(32+56)+count*64+sum(len(p.encode())+1 for p in images))
    methods = sum(len(r['methods']) for r in inventory['method_lists'])
    class_count = len(inventory['classes'])
    categories = len(inventory['categories'])+len(transfer['incoming'])
    slot_sizes = {
        'rw':align(count*8+4096), 'new_protocol':4096,
        'provider_rw':align(max(4096,class_count*1024+categories*256)),
        'selector':align(source_tables['selector']['size']*2+len(missing['selectors'])*128+4096),
        'class':align(source_tables['class']['size']*2+class_count*128+4096),
        'protocol':align(source_tables['protocol']['size']*2+len(inventory['protocols'])*128+4096),
        'ro':align(count*32+4096), 'imageinfo':align(count*8+4096),
        'category_lists':align(max(65536,methods*48+categories*1024)),
        'path_trie':align(max(65536,sum(len(p.encode())+16 for p in images)*4)),
        'patch_table':align(max(65536,patch_size*2+sum(p['import_inventory'].get('records',0) if type(p['import_inventory'].get('records',0)) is int else 0 for p in plan['providers'])*64)),
        'cached_pbl':align(cached_size*2+len(plan['providers'])*65536),
        # Twice the source pool plus 4 KiB per provider class plan. build_pool refuses a pool that outgrows it; the measured
        # final pool is 17.9 MB (46,562-record catalog) against the 196 MB that 64 KiB per plan reserved at 66,118 records,
        # which pushed the fresh region out of legacy_objc_root's +-2 GiB.
        'program_pbl':align(program_pool_size*2+len(plan['providers'])*len(transfer.get('class_plans',[]))*4096),
        'program_trie':align(max(65536,program_pool_size//8)),
        'atlas':align(max(65536,atlas_size*2)),
    }
    for group,slot in [('selectors','selector_names'),('classes','class_names'),('protocols','protocol_names')]:
        slot_sizes[slot] = align(max(4096,sum(len(n.encode())+1 for n in missing[group])))
    # The selector pool also holds the providers' small-method type strings: in-cache small methods name their types by an
    # offset from the selector base, and a folded provider's own strings can lie beyond its +-2 GiB.
    slot_sizes['selector_names'] = align(slot_sizes['selector_names']+sum(len(s.encode())+1 for s in method_type_strings(inventory)))
    if swift_sizes:
        slot_sizes.update({name: align(max(4096, size)) for name, size in swift_sizes.items()})
    # Slide starts/chain metadata have a page-count-derived upper reservation.
    slot_sizes['slide_info'] = align(4096+sum(slot_sizes.values())//4096*4)
    require(set(slot_sizes) == SLOTS | set(swift_sizes or {}), 'internal metadata slot contract differs')
    executable = ('swift_runtime_code',) if 'swift_runtime_code' in slot_sizes else ()
    header_size=prefix
    prefix+=sum(slot_sizes[k] for k in executable)
    writable = ('rw','new_protocol','provider_rw') + (('swift_metadata',) if swift_sizes else ())
    if 'swift_runtime_data' in slot_sizes: writable+=('swift_runtime_data',)
    read_only = tuple(k for k in slot_sizes if k not in writable+executable)
    rw_size = sum(slot_sizes[k] for k in writable); ro_size = sum(slot_sizes[k] for k in read_only)
    extent = prefix+rw_size+ro_size
    occupied = [(r['address'],r['address']+r['size']) for r in plan['original_mapping_records']]
    occupied += [(m['address'],m['address']+m['size']) for p in plan['providers'] for m in p['placement']['mappings']]
    occupied += [(r['address'],r['address']+r['size']) for r in plan['preserved_nonfile_reservations']]
    first = plan['original_mapping_records'][0]
    occupied.append((first['address']+first['size'],first['address']+first['size']+plan['base_layout']['required_main_text_growth']))
    region = plan['shared_region']; cursor = align(region['start'])
    if swift_code_anchor is not None:
        cursor=max(cursor,align(swift_code_anchor-(1<<31)+4096))
    for lo,hi in sorted(occupied):
        if cursor+extent <= lo: break
        if cursor < hi: cursor = align(hi)
    require(cursor+extent+region['max_slide'] <= region['start']+region['size'], 'fresh metadata allocation exceeds shared region')
    if swift_code_anchor is not None:
        require(cursor+prefix-swift_code_anchor<(1<<31),'witness runtime allocation exceeds rel32 entry reach')
    layout = dict(schema=1,classification='current-run measured reservations; content fit and final oracles required',
                  prefix_size=prefix,final_images=count,final_subcaches=plan['main_updates']['final_cache_file_count'],
                  subcache_suffix=f".{plan['main_updates']['final_cache_file_count']:02d}",
                  selector_base=baseline['selector_base'],legacy_objc_root=baseline['legacy_objc_root'],mappings=[])
    address = cursor; offset = 0
    for size,protection in ((prefix,5),(rw_size,3),(ro_size,1)):
        layout['mappings'].append(dict(address=address,size=size,file_offset=offset,maximum=protection,initial=protection,flags=0))
        address += size; offset += size
    for group,start in ((executable,cursor+header_size),(writable,cursor+prefix),(read_only,cursor+prefix+rw_size)):
        for key in group:
            layout[key] = dict(address=start,reserve=slot_sizes[key]); start += slot_sizes[key]
    for key in ('selector','class','protocol','ro','rw'):
        require(-(1<<31) <= layout[key]['address']-layout['legacy_objc_root'] < 1<<31,'fresh ObjC allocation exceeds relative root range')
    require(-(1<<31) <= layout['selector_names']['address']-layout['selector_base']
            and layout['selector_names']['address']+layout['selector_names']['reserve']-layout['selector_base'] <= 1<<31,
            'fresh selector pool exceeds signed relative range')
    return layout,missing


def prepare_metadata(preparation_report, structural_manifest, output, *, preparation_sha256, structural_sha256,
                     swift_catalog=None, swift_runtime_toolchain=None):
    """Emit allocation, fresh name pools and checked provider ObjC recipe."""
    preparation_report = Path(preparation_report).resolve(strict=True)
    structural_manifest = Path(structural_manifest).resolve(strict=True)
    require(pin(preparation_report)['sha256'] == preparation_sha256 and pin(structural_manifest)['sha256'] == structural_sha256,
            'preparation/structural report pin differs')
    prep = json.loads(preparation_report.read_text())
    require(prep.get('kind') == 'dyld-cache-build/producer-preparation' and prep.get('schema') == 1, 'unknown producer preparation')
    for record in prep['artifacts'].values(): _verified(record)
    for record in prep['intake_pins'].values(): _verified(record)
    def artifact(name): return Path(prep['artifacts'][name]['path'])
    plan_path = artifact('provider-plan.json'); plan = json.loads(plan_path.read_text())
    structural = json.loads(structural_manifest.read_text())
    require(structural['producer_plan_sha256'] == pin(plan_path)['sha256'],'structural provider-plan pin differs')
    main,files,images,structural_pins = cache_manifest(structural_manifest)
    require((images,files) == (plan['main_updates']['final_image_count'],plan['main_updates']['final_cache_file_count']), 'structural graph counts differ')
    capture = json.loads(Path(prep['intake_pins']['capture-manifest.json']['path']).read_text())
    baseline = json.loads(artifact('source-metadata/objc-baseline.json').read_text())
    transfer = json.loads(artifact('objc-transfer.json').read_text())
    out = Path(output).resolve()
    provenance = json.loads(Path(prep['intake_pins']['run-provenance.json']['path']).read_text())
    require(not out.exists() and not any(out.is_relative_to(Path(provenance[k])) for k in ('pristine_os','patched_system')),
            'metadata output must be fresh and outside sources')
    out.mkdir(parents=True)
    inventory = provider_inventory(plan,Path('/')) # every input path is absolute and run-pinned
    original = CandidateFamily(capture['main'],image_count=capture['images'],subcache_count=capture['cache_files']-1)
    candidate = None
    try:
        candidate = CandidateFamily(main,image_count=images,subcache_count=files-1)
        required = {r['path'] for r in plan['path_trie']['replacements']}
        inventory['retired_classes'] = retired_classes(original,required)
        final_order = objc_inventory(candidate)
        # objc_inventory returns identities in increasing Mach-O address order.
        final_order = [row['path'] for row in final_order]
        _,program_size = struct.unpack_from('<QQ',original.main.data,336)
        patch_size = struct.unpack_from('<Q',original.main.data,160)[0]
        cached = json.loads(artifact('source-metadata/cached-loader-baseline.json').read_text())
        atlas = next(r for r in capture['files'] if r['path'].endswith('.atlas'))
        swift_sizes = None; swift_code_anchor = None
        if swift_catalog:
            from .swift_metadata import pointer_entries, prepare, table_reservations, RECORD_SIZE
            catalog = json.loads(Path(swift_catalog).read_text())
            entries, _ = pointer_entries(candidate)
            count = len(catalog['records'])
            rows=catalog['records']+catalog.get('witnesses',[])
            metadata_size = (sum(len(bytes.fromhex(r['data']))+4096*any(p['offset']%8 for p in r['pointers']) for r in rows)
                             if catalog.get('schema') in (2, 3, 4) else count * RECORD_SIZE)
            tables = table_reservations(catalog, entries, out/'swift-sizing')
            swift_sizes = dict(swift_metadata=metadata_size, swift_table=tables['metadata'])
            if catalog.get('schema')==4 and (catalog['witnesses'] or any(
                    r.get('record_kind')=='metatype' for r in catalog['records'])):
                from .witness_runtime import profile as runtime_profile
                require(swift_runtime_toolchain is not None,'dependency catalogs require --swift-runtime-toolchain')
                swift_code_anchor=runtime_profile(candidate)['entry']
                swift_sizes.update(swift_witness_table=tables.get('witness', 0),
                                   swift_runtime_code=8192,swift_runtime_data=4096)
                metatypes=sum(r.get('record_kind')=='metatype' for r in catalog['records'])
                if metatypes:swift_sizes['swift_metatype_table']=tables['metatype']
                if 'normalized' in tables:
                    swift_sizes['swift_normalized_table']=tables['normalized']
                    descriptors={json.dumps(r['key'][0],sort_keys=True) for r in catalog['records']
                                 if 'type_arguments' in r and len(r['key'])>r['type_arguments']+1}
                    swift_sizes['swift_runtime_data']+=16*len(descriptors)
        layout,missing = allocate_metadata(plan,baseline,inventory,transfer,program_pool_size=program_size,
                                          cached_size=cached.get('length') or cached['decoded']['length'],patch_size=patch_size,atlas_size=atlas['size'],
                                          swift_sizes=swift_sizes,swift_code_anchor=swift_code_anchor)
        layout['provider_plan_sha256'] = pin(plan_path)['sha256']
        geometry = validate(layout,candidate,plan)
        pools = out/'canonical-additions'; pools.mkdir()
        if swift_catalog:
            swift_recipe = prepare(catalog, candidate, layout, out / 'swift',runtime_toolchain=swift_runtime_toolchain)
            swift_recipe['catalog'] = pin(swift_catalog)
            _json(out / 'swift/recipe.json', swift_recipe)
        allocation_pin = _json(out/'metadata-layout.json',layout)
        missing_pin = _json(out/'new-names.json',missing)
        names = dict(schema=1,pools={},allocation_sha256=allocation_pin['sha256'],missing_names_sha256=missing_pin['sha256'])
        maps = {}
        for group,kind,slot in [('selectors','selector','selector_names'),('classes','class','class_names'),('protocols','protocol','protocol_names')]:
            canonical = json.loads(artifact('source-metadata/'+kind+'-canonical.json').read_text())
            _json(out/(kind+'-canonical.json'),canonical)
            data,additions = extend_pool(b'',layout[slot]['address'],layout[slot]['reserve'],{},missing[group])
            if group == 'selectors':
                # method type strings follow the new selector names in the same pool (a string equal to one is shared)
                strings = method_type_strings(inventory)
                data,with_types = extend_pool(data,layout[slot]['address'],layout[slot]['reserve'],additions,
                                              [s for s in strings if s not in additions])
                method_types = {s: with_types[s] for s in strings}
            path = pools/(group+'.bin'); path.write_bytes(data)
            names[group] = additions; maps[group] = {**canonical,**additions}
            names['pools'][group] = dict(address=hex(layout[slot]['address']),bytes=len(data),reserve=layout[slot]['reserve'],
                                        path=str(path),sha256=pin(path)['sha256'])
        names_pin = _json(pools/'names.json',names)
        protocol_objects = json.loads(artifact('source-metadata/protocol-objects.json').read_text())
        _json(out/'protocol-objects.json',protocol_objects)
        protocol_objects.update({n:layout['new_protocol']['address'] for n in missing['protocols']})
        graph = FinalGraph(original,{})
        resolved = graph.resolve('/usr/lib/libobjc.A.dylib','_OBJC_CLASS_$_Protocol')
        require(resolved['status'] == 'resolved' and resolved['kind'] == 'regular', 'Protocol class does not resolve to regular export')
        protocol_class = dict(original.images)[resolved['provider']]+int(resolved['input_offset'],16)
        result = emit_recipe(candidate,original,plan,transfer,inventory,layout,maps['selectors'],maps['classes'],
                             maps['protocols'],protocol_objects,final_order,protocol_class,method_types=method_types)
        order_pin = _json(out/'final-objc-order.json',final_order)
        result['inputs'] = dict(structural_family=structural_pins,base_report_sha256=structural_sha256,
            provider_layout_sha256=pin(plan_path)['sha256'],category_transfer_path=str(artifact('objc-transfer.json')),category_transfer_sha256=pin(artifact('objc-transfer.json'))['sha256'],
            allocation_sha256=allocation_pin['sha256'],canonical_additions_sha256=names_pin['sha256'],
            final_header_order_sha256=order_pin['sha256'],base_subcache_count=files-1,
            canonical_directory=str(out),allocation_path=str(out/'metadata-layout.json'),provider_layout_path=str(plan_path),
            final_header_order_path=str(out/'final-objc-order.json'),
            canonical_source_hashes={name:pin(out/name)['sha256'] for name in ('canonical-additions/names.json','selector-canonical.json','class-canonical.json','protocol-canonical.json','protocol-objects.json')})
        tables = dict(schema=1,replaced_images=sorted(required),
            classes=[dict(name=r['name'],address=r['address'],name_address=maps['classes'][r['name']],image=r['image']) for r in inventory['classes']],
            protocols=[dict(name=r['name'],address=protocol_objects[r['name']],name_address=maps['protocols'][r['name']],image=r['image']) for r in inventory['protocols']],
            selectors=[dict(name=n,address=a) for n,a in names['selectors'].items()],
            imageinfo=[dict(image=r['path'],bytes_hex=(bytes(4)+(r['image_info']['source_flags']|9).to_bytes(4,'little')).hex()) for r in inventory['images'] if r['image_info']])
        component = out/'provider-objc'; component.mkdir()
        for name,value in [('recipe.json',result),('inventory.json',inventory),('tables-input.json',tables)]: _json(component/name,value)
        for i,blob in enumerate(result['blobs']): (component/f'blob-{i:03d}.bin').write_bytes(bytes.fromhex(blob['data']))
    finally:
        if candidate is not None: candidate.close()
        original.close()
    for record in prep['artifacts'].values(): _verified(record)
    for record in structural_pins: _verified(record)
    outputs = {str(p.relative_to(out)):pin(p) for p in sorted(out.rglob('*')) if p.is_file()}
    report = dict(schema=1,kind='dyld-cache-build/metadata-preparation',build=prep['build'],
                  classification='checked current-run ObjC recipe and name pools; independent emission/output oracles not checked here',
                  outputs=outputs,geometry=geometry,preparation=pin(preparation_report),structural=pin(structural_manifest),
                  source_modules={p.name:pin(p) for p in Path(__file__).parent.glob('*.py')},certification_complete=False)
    return dict(report=_json(out/'metadata-preparation.json',report),artifacts=outputs)
