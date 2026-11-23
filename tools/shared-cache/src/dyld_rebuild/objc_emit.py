"""Checked Objective-C byte edits/blobs for the structural provider family.

Plans operate on decoded pointer targets, never on v2 chain-next bits. The root
writer owns allocation, chain rebuilding, identities, signatures and publication.
"""
from bisect import bisect_right
from collections import defaultdict
import hashlib
import json
import struct
from .cache import commands
from .exports import Refusal, require
from .objc_transfer import KINDS, Reader, sections
from .provider_layout import Provider
from .slides import family_slides


def family_selector_base(family):
    """The input cache's own selector base (ObjC optimization header field 7; v2 on 26, v4 on 27)."""
    from .objc_tables import objc_header
    base=family.main.mappings[0].address
    return base+objc_header(family)[2][7]


class FamilyReader(Reader):
    def __init__(self,family,selector_base=None):
        self.family=family;self._selector_base=selector_base
        self.maps=sorted((m.address,m.address+m.size,f,m) for f in family.files for m in f.mappings)
        self.starts=[r[0] for r in self.maps]
        self.slides={m.address:s for f,m,s in family_slides(family)}

    @property
    def selector_base(self):
        # No build's base is assumed: it comes from the caller's layout or, on first use, from this family.
        if self._selector_base is None: self._selector_base=family_selector_base(self.family)
        return self._selector_base

    @selector_base.setter
    def selector_base(self,value): self._selector_base=value

    def locate(self,address,size=1):
        i=bisect_right(self.starts,address)-1
        require(i>=0 and address+size<=self.maps[i][1],'unmapped ObjC address')
        return self.maps[i]

    def read(self,address,size):
        lo,hi,file,mapping=self.locate(address,size)
        off=mapping.offset+address-lo
        return file.data[off:off+size]

    def ptr(self,address):
        lo,hi,file,mapping=self.locate(address,8)
        slide=self.slides.get(lo)
        if slide is not None:
            offset=address-lo
            page=slide.page(offset//4096)
            if offset in page:return slide.decode(page[offset])
        return struct.unpack('<Q',self.read(address,8))[0]

    def string(self,address):
        lo,hi,file,mapping=self.locate(address)
        off=mapping.offset+address-lo
        end=file.data.find(b'\0',off,min(off+65536,mapping.offset+mapping.size))
        require(end>=off,'unterminated ObjC string')
        try:return file.data[off:end].decode('utf-8')
        except UnicodeDecodeError as exc:raise Refusal('non-UTF8 ObjC name') from exc

    def string_bytes(self,address):
        lo,hi,file,mapping=self.locate(address)
        off=mapping.offset+address-lo
        end=file.data.find(b'\0',off,min(off+65536,mapping.offset+mapping.size))
        require(end>=off,'unterminated ObjC string')
        return bytes(file.data[off:end])


def provider_inventory(layout,root):
    classes=[];protocols=[];categories=[];method_lists={};selector_refs=[];images=[]
    def add_methods(reader,address,path,slide,owner):
        if not address:return
        for entry in reader.lists(address):
            target=entry['address']
            if (path,target) not in method_lists:
                decoded=reader.methods(target)
                flags,count=struct.unpack('<II',reader.read(target,8))
                small=bool(flags&0x80000000);stride=12 if small else 24
                rows=[]
                for index,method in enumerate(decoded['entries']):
                    entry=target+8+index*stride
                    if small:
                        sel,types,imp=struct.unpack('<iii',reader.read(entry,12))
                        selector=reader.ptr(entry+sel)
                        # 0x20000000: types are offsets from the selector base (27 cache lists).
                        types_address=(reader.selector_base+(types&0xffffffff)) if flags&0x20000000 else entry+4+types
                    else:
                        selector=reader.ptr(entry);types_address=reader.ptr(entry+8)
                    source_imp=int(method['imp'],16)
                    rows.append(dict(method,source_index=index,source_entry=entry,entry=entry+slide,
                        source_selector=selector,source_types_address=types_address,types_address=types_address+slide,
                        source_imp=source_imp,imp=source_imp+slide if source_imp else 0))
                method_lists[path,target]={'image':path,'source_address':target,'address':target+slide,
                    'flags':flags,'count':count,'small':small,'owners':[],'methods':rows}
            method_lists[path,target]['owners'].append(owner)
    for image in layout['providers']:
        path=image['path'];slide=image['placement']['uniform_slide'];raw=(root/image['input_path']).read_bytes()
        provider=Provider(image['key'],raw,path)
        require(provider.container_offset==image['slice_offset'] and len(provider.data)==image['input_size']
                and hashlib.sha256(provider.data).hexdigest()==image['input_sha256'],'provider inventory input slice hash drift')
        reader=provider.reader
        image_row={'path':path,'key':image['key'],'header':image['placement']['provider_header_address'],
                   'image_info':None,'class_count':0,'protocol_count':0,'category_count':0}
        for section in provider.sections:
            name,address,size=section['name'],section['address'],section['size']
            if name=='__objc_imageinfo':
                require(size==8,'unsupported image info size')
                image_row['image_info']={'address':address+slide,'source_flags':reader.number(address+4,'<I')}
            if name=='__objc_classlist':
                require(size%8==0,'unaligned provider classlist')
                for field in range(address,address+size,8):
                    cls=reader.ptr(field);ro=reader.ptr(cls+32)&~7
                    cname=reader.string(reader.ptr(ro+24));isa=reader.ptr(cls)
                    row={'image':path,'name':cname,'source_address':cls,'address':cls+slide,
                         'classlist_field':field+slide,'source_ro':ro,'ro':ro+slide,
                         'source_metadata':reader.class_info(cls),'metaclass':isa+slide,
                         'source_metaclass':reader.class_info(isa)}
                    classes.append(row);image_row['class_count']+=1
                    for value,kind in ((cls,'class'),(isa,'metaclass')):
                        data=reader.ptr(value+32)&~7
                        add_methods(reader,reader.ptr(data+32),path,slide,{'kind':kind,'class':cname})
            elif name=='__objc_protolist':
                require(size%8==0,'unaligned provider protolist')
                for field in range(address,address+size,8):
                    proto=reader.ptr(field);psize=reader.number(proto+64,'<I')
                    require(psize in (72,80,88,96),'unsupported protocol size')
                    pname=reader.string(reader.ptr(proto+8))
                    protocols.append({'image':path,'name':pname,'source_address':proto,'address':proto+slide,
                        'protolist_field':field+slide,'size':psize,'flags':reader.number(proto+68,'<I')})
                    image_row['protocol_count']+=1
                    for offset,kind in ((24,'instance'),(32,'class'),(40,'optional_instance'),(48,'optional_class')):
                        add_methods(reader,reader.ptr(proto+offset),path,slide,{'kind':'protocol.'+kind,'protocol':pname})
            elif name in ('__objc_catlist','__objc_nlcatlist'):
                require(size%8==0,'unaligned provider catlist')
                for field in range(address,address+size,8):
                    cat=reader.ptr(field);cname=reader.string(reader.ptr(cat));target=reader.reference(cat+8)
                    row={'image':path,'name':cname,'address':cat+slide,'source_address':cat,
                         'catlist_field':field+slide,'class_reference':target,'lists':[]}
                    for kind,offset,_,_ in KINDS:
                        value=reader.ptr(cat+offset)
                        if value:row['lists'].append({'kind':kind,'source_address':value,'address':value+slide})
                        if kind.endswith('Methods'):add_methods(reader,value,path,slide,{'kind':'category.'+kind,'category':cname})
                    categories.append(row);image_row['category_count']+=1
            elif name=='__objc_selrefs':
                require(size%8==0,'unaligned provider selector refs')
                for field in range(address,address+size,8):
                    selector_refs.append({'image':path,'address':field+slide,
                        'selector':reader.string(reader.ptr(field)),'target':reader.ptr(field)+slide})
        images.append(image_row)
    methods=list(method_lists.values())
    selectors=sorted({m['selector'] for row in methods for m in row['methods']}|{r['selector'] for r in selector_refs})
    return {'schema':1,'images':images,'classes':classes,'protocols':protocols,'categories':categories,
            'method_lists':methods,'selector_refs':selector_refs,'selectors':selectors}


def retired_classes(original,targets):
    reader=FamilyReader(original);result=[]
    for path,mh in original.images:
        if path not in targets:continue
        for _,name,address,size in sections(commands(original,mh)):
            if name!='__objc_classlist':continue
            for field in range(address,address+size,8):
                cls=reader.ptr(field);ro=reader.ptr(cls+32)&~7
                raw=reader.string_bytes(reader.ptr(ro+24))
                try:name=raw.decode('utf-8')
                except UnicodeDecodeError:name=None
                result.append({'image':path,'name':name,'name_hex':raw.hex(),'address':cls,
                               'metaclass':reader.ptr(cls),'ro':ro})
    return result


def encode_small_methods(row,selectors,selector_base,method_types=None):
    require(row['small'] and len(row['methods'])==row['count'],'small method shape differs')
    order=sorted(range(row['count']),key=lambda i:selectors[row['methods'][i]['selector']])
    # The stock in-cache form (0xe000000f): selectors and types are offsets from the selector base, with the
    # type-offsets flag (0x20000000). 27's libobjc reads every small list inside the shared cache that way,
    # whatever the flag says, so an entry-relative types field decodes to another string.
    header=struct.pack('<II',row['flags']|0x60000003,row['count']);body=bytearray(header)
    for new_index,old_index in enumerate(order):
        method=row['methods'][old_index];field=row['address']+8+new_index*12
        types=method_types[method['types']] if method_types is not None else method['types_address']
        offsets=(selectors[method['selector']]-selector_base,types-selector_base,method['imp']-(field+8))
        require(all(-(1<<31)<=offset<(1<<31) for offset in offsets),'small method signed32 range overflow')
        require(offsets[1]>=0,'method types below the selector base (not representable as an unsigned offset)')
        require(method['imp']!=0,'nullable IMP in small method list')
        body+=struct.pack('<iii',*offsets)
    require(len(body)==8+row['count']*12,'small method encoded length differs')
    return bytes(body),order


def cstring(read,address,limit=4096):
    # 64-byte steps, then single bytes near a mapping's end, so the read never runs past the string's mapping
    out=bytearray()
    while len(out)<limit:
        try:chunk=read(address+len(out),64)
        except ValueError:chunk=read(address+len(out),1)
        end=chunk.find(b'\0')
        if end>=0:return bytes(out+chunk[:end])
        out+=chunk
    require(False,'unterminated string')


def check_small_method_types(read,selector_base,address,before):
    """Decode the small method list at ADDRESS as 27's libobjc does inside the shared cache (types =
    selector base + u32 offset, whatever the flags say) and compare each method's types with the list it replaced
    (BEFORE: the source list's bytes, entry-relative types), pairing methods by IMP. Returns the mismatches."""
    flags,count=struct.unpack('<II',read(address,8));old_flags,old_count=struct.unpack_from('<II',before)
    require(flags&0x80000000 and flags&0x20000000 and count==old_count,'in-cache small list lacks the stock form')
    def entries(base,data,cached):
        out={}
        for i in range(count):
            entry=base+8+12*i;_,types,imp=struct.unpack_from('<iii',data,8+12*i)
            types_address=selector_base+(types&0xffffffff) if cached else entry+4+types
            out[entry+8+imp]=types_address
        return out
    before_types=entries(address,before,bool(old_flags&0x20000000))
    after_types=entries(address,read(address,8+12*count),True)
    require(set(before_types)==set(after_types),'method IMPs differ after the rewrite')
    return [(imp,cstring(read,before_types[imp]),cstring(read,after_types[imp])) for imp in sorted(after_types)
            if cstring(read,before_types[imp])!=cstring(read,after_types[imp])]


def relative_list_blob(address,entries):
    require(address%8==0 and entries and len(entries)<65536,'invalid list-list geometry')
    blob=bytearray(struct.pack('<II',8,len(entries)))
    for i,entry in enumerate(entries):
        target,index=entry['list'],entry['image_index'];field=address+8+8*i
        delta=target-field
        require(0<=index<65536 and -(1<<47)<=delta<(1<<47),'relative list-list index/delta overflow')
        blob+=struct.pack('<Q',((delta&((1<<48)-1))<<16)|index)
    return bytes(blob)


class Recipe:
    def __init__(self,reader,allocation):
        self.reader=reader;self.allocation=allocation;self.byte_edits={};self.pointer_edits={}
        self.blobs=[];self.new_pointers={};self.cursors={key:row['address'] for key,row in allocation.items()
            if isinstance(row,dict) and 'address' in row and 'reserve' in row}
    def byte(self,address,data,reason):
        before=self.reader.read(address,len(data))
        if before==data:return
        if address in self.byte_edits:
            require(self.byte_edits[address]['after']==data.hex(),'conflicting byte edits')
            return
        for old in self.byte_edits.values():
            require(address+len(data)<=old['address'] or old['address']+len(bytes.fromhex(old['after']))<=address,
                    'overlapping byte edits')
        self.byte_edits[address]={'address':address,'before':before.hex(),'after':data.hex(),'reason':reason}
    def pointer(self,address,target,reason):
        before=self.reader.ptr(address)
        lo,hi,file,mapping=self.reader.locate(address,8);slide=self.reader.slides.get(lo)
        member=slide is not None and address-lo in slide.page((address-lo)//4096)
        if before==target:return
        if address in self.pointer_edits:
            require(self.pointer_edits[address]['target']==target,'conflicting pointer edits')
            return
        self.pointer_edits[address]={'address':address,'expected':before,'target':target,
            'member_before':member,'member_after':target!=0,'reason':reason}
    def blob(self,key,data,reason,alignment=8):
        require(key in self.cursors,'unknown metadata reservation')
        address=(self.cursors[key]+alignment-1)&~(alignment-1)
        require(address+len(data)<=self.allocation[key]['address']+self.allocation[key]['reserve'],
                'metadata reservation overflow: '+key)
        choices=[m for m in self.allocation['mappings'] if m['address']<=address and address+len(data)<=m['address']+m['size']]
        require(len(choices)==1,'blob has no unique metadata mapping')
        required=3 if key in ('new_protocol','provider_rw') else 1
        require(choices[0]['initial']==required and not choices[0]['maximum']&4,'metadata blob protection differs')
        self.cursors[key]=address+len(data)
        self.blobs.append({'reservation':key,'address':address,'data':data.hex(),'size':len(data),
                           'sha256':hashlib.sha256(data).hexdigest(),'reason':reason})
        return address
    def blob_pointer(self,address,target,reason):
        require(address not in self.new_pointers or self.new_pointers[address]['target']==target,'conflicting new pointer field')
        if target:self.new_pointers[address]={'address':address,'target':target,'reason':reason}
    def finish(self):
        for edit in self.pointer_edits.values():
            for byte in self.byte_edits.values():
                require(edit['address']+8<=byte['address'] or byte['address']+len(bytes.fromhex(byte['after']))<=edit['address'],
                        'pointer and byte edits overlap')
        return {'byte_edits':sorted(self.byte_edits.values(),key=lambda r:r['address']),
            'pointer_edits':sorted(self.pointer_edits.values(),key=lambda r:r['address']),
            'blobs':self.blobs,'new_pointer_fields':sorted(self.new_pointers.values(),key=lambda r:r['address']),
            'reservation_usage':{key:self.cursors[key]-self.allocation[key]['address'] for key in self.cursors
                                  if self.cursors[key]!=self.allocation[key]['address']}}


def method_edits(recipe,inventory,selectors,selector_base,method_types=None):
    permutations={};lists={r['address']:r for r in inventory['method_lists']}
    for row in inventory['method_lists']:
        require(all(m['selector'] in selectors for m in row['methods']),'canonical selector missing')
        if row['small']:
            data,order=encode_small_methods(row,selectors,selector_base,method_types)
            recipe.byte(row['address'],data,'canonical direct small methods, stable SEL sort and fixed-up flags')
        else:
            order=sorted(range(row['count']),key=lambda i:selectors[row['methods'][i]['selector']])
            recipe.byte(row['address'],struct.pack('<I',row['flags']|3),'fixed-up pointer method list')
            for new_index,old_index in enumerate(order):
                method=row['methods'][old_index];field=row['address']+8+24*new_index
                for offset,target,role in ((0,selectors[method['selector']],'selector'),(8,method['types_address'],'types'),(16,method['imp'],'IMP')):
                    recipe.pointer(field+offset,target,'stable sorted pointer method '+role)
        permutations[row['address']]=order
    # Extended protocol type arrays are concatenated in the four method-list
    # groups. Sorting each group requires the same permutation in its type array.
    for proto in inventory['protocols']:
        address=proto['address'];extended=recipe.reader.ptr(address+72) if proto['size']>=80 else 0
        if not extended:continue
        cursor=0
        for offset in (24,32,40,48):
            methods=recipe.reader.ptr(address+offset)
            if not methods:continue
            require(methods in lists,'protocol method list absent from inventory')
            order=permutations[methods];count=len(order)
            values=[recipe.reader.ptr(extended+8*(cursor+i)) for i in range(count)]
            for new_index,old_index in enumerate(order):
                recipe.pointer(extended+8*(cursor+new_index),values[old_index],'extended protocol type follows stable method sort')
            cursor+=count
    return permutations


def growth_edits(recipe,inventory,transfer):
    classes={r['address']:r for r in inventory['classes']};state={};projections={}
    from .objc_transfer import project_ivar_growth
    def size(address):
        if address not in classes:
            ro=recipe.reader.ptr(address+32)&~7
            return recipe.reader.number(ro+8,'<I')
        if state.get(address)==2:return projections[address]['instance_size']
        require(state.get(address)!=1,'provider superclass cycle')
        state[address]=1;row=classes[address];parent=recipe.reader.ptr(address+8)
        require(parent!=0,'unexpected provider root class')
        projected=project_ivar_growth(row['source_metadata'],size(parent))
        projections[address]=projected;state[address]=2
        if projected['delta']:
            recipe.byte(row['ro']+4,struct.pack('<II',projected['instance_start'],projected['instance_size']),
                        'superclass growth preserves inherited storage and ivar alignment')
            for name,new_offset in projected['ivar_offsets'].items():
                source=int(row['source_metadata']['ivar_offset_storage'][name],16)
                slide=row['address']-row['source_address']
                recipe.byte(source+slide,struct.pack('<I',new_offset),'inherited ivar growth: '+row['name']+'.'+name)
        return projected['instance_size']
    for address in classes:size(address)
    for reference in transfer['external_class_references']:
        if reference['kind']!='class.superclass' or reference['replacement_export']['status']=='missing_symbol':continue
        resolution=reference['replacement_export'];provider=resolution['provider']
        candidates=[r for r in inventory['classes'] if r['image']==provider and r['name']==reference['class']]
        require(len(candidates)==1,'external superclass replacement identity ambiguous')
        final_size=projections[candidates[0]['address']]['instance_size']
        require(final_size<=reference['source_instance_size'],
                'external superclass grew beyond original layout: whole descendant graph projection required')
        require(reference['subclass']['instance_start']>=final_size,'external subclass storage overlaps final superclass')
    return [{'class':classes[address]['name'],'image':classes[address]['image'],'address':address,**projection}
            for address,projection in projections.items() if projection['delta']]


def canonical_protocol_edits(recipe,inventory,layout,protocol_objects,protocol_names,new_protocol_address,protocol_class):
    aliases={r['address']:protocol_objects[r['name']] for r in inventory['protocols']}
    rows_by_name=defaultdict(list)
    for row in inventory['protocols']:rows_by_name[row['name']].append(row)
    for image in layout['providers']:
        fixes=image['pointer_fixups']
        for values in fixes['rebase_rows']:
            row=dict(zip(fixes['rebase_columns'],values))
            if row['target'] in aliases:
                recipe.pointer(row['address'],aliases[row['target']],'canonical protocol object reference')
        for values in fixes['bind_rows']:
            row=dict(zip(fixes['bind_columns'],values));binding=fixes['bindings'][row['binding_index']]
            if binding['target'] in aliases:
                recipe.pointer(row['address'],aliases[binding['target']],'canonical bound protocol object reference')
    new_names=[name for name,address in protocol_objects.items() if address==new_protocol_address]
    require(len(new_names)==1 and new_names[0] in rows_by_name,'new canonical protocol identity missing/ambiguous')
    name=new_names[0];source=rows_by_name[name][0];address=source['address']
    require(source['size']==96,'new canonical protocol requires explicit size extension')
    body=bytearray(96);flags=(source['flags']&~0xe0000000)|0x60000000
    struct.pack_into('<II',body,64,96,flags)
    result=recipe.blob('new_protocol',body,'new canonical protocol '+name)
    require(result==new_protocol_address,'new protocol allocation differs from canonical table contract')
    for offset in (0,8,16,24,32,40,48,56,72,80,88):
        value=recipe.reader.ptr(address+offset)
        if offset==0:value=protocol_class
        elif offset in (8,80):value=protocol_names[name]
        if value in aliases:value=aliases[value]
        recipe.blob_pointer(result+offset,value,'canonical protocol pointer field '+str(offset))
    return {'name':name,'source_address':address,'address':result,'size':96,'flags':flags}


def original_outgoing_categories(original,replaced_images):
    reader=FamilyReader(original);result=[]
    for image,mh in original.images:
        if image not in replaced_images:continue
        for _,name,address,size in sections(commands(original,mh)):
            if name not in ('__objc_catlist','__objc_nlcatlist'):continue
            for field in range(address,address+size,8):
                cat=reader.ptr(field);cls=reader.ptr(cat+8);ro=reader.ptr(cls+32)&~7
                row={'image':image,'name':reader.string(reader.ptr(cat)),'address':cat,
                     'class':reader.string(reader.ptr(ro+24)),'target':cls,'lists':[]}
                for kind,offset,_,_ in KINDS:
                    value=reader.ptr(cat+offset)
                    if value:row['lists'].append({'kind':kind,'list':value})
                result.append(row)
    return result


def class_owners(original,addresses):
    remaining=set(addresses);owners={};reader=FamilyReader(original)
    for image,mh in original.images:
        if not remaining:break
        for _,name,address,size in sections(commands(original,mh)):
            if name!='__objc_classlist':continue
            for field in range(address,address+size,8):
                value=reader.ptr(field)
                if value in remaining:owners[value]=image;remaining.remove(value)
    require(not remaining,'unaffected category class owner missing from original classlists')
    return owners


def category_edits(recipe,inventory,transfer,original,final_indexes,replaced):
    from .objc_transfer import CachedReader
    old_indexes=CachedReader(original).objc_images
    new_classes={r['name']:r for r in inventory['classes']}
    new_owner={r['address']:r['image'] for r in inventory['classes']}
    new_owner.update({r['metaclass']:r['image'] for r in inventory['classes']})
    # Tahoe builds replaced 3 or 6 images; 26B131's fold-in replaces 9-10. Per-category checks follow below.
    require(0<len(replaced)<128,'replacement category-owner contract differs')
    outgoing=original_outgoing_categories(original,replaced)
    owners=class_owners(original,{r['target'] for r in outgoing})
    groups={};records=[]
    kind_map={kind:(is_meta,ro_offset) for kind,_,is_meta,ro_offset in KINDS}
    def group(cls,kind,name):
        key=cls,kind
        if key not in groups:
            meta,offset=kind_map[kind]
            target=recipe.reader.ptr(cls) if meta else cls
            ro=recipe.reader.ptr(target+32)&~7
            base=recipe.reader.ptr(ro+offset)
            owner=new_owner.get(target) or owners.get(cls)
            require(owner is not None,'category target defining image unknown')
            entries=[]
            for entry in recipe.reader.lists(base):
                image=owner if entry['image_index'] is None else old_indexes[entry['image_index']]
                entries.append({'list':entry['address'],'image':image,'image_index':final_indexes[image],
                                'origin':'replacement_base' if cls in new_owner else 'preserved_existing'})
            groups[key]={'class':name,'class_address':cls,'kind':kind,'target_class':target,
                'ro_field':ro+offset,'base_owner':owner,'entries':entries,'incoming':[],
                'provider_categories':[],'retired':[],'retired_positions':[]}
        return groups[key]
    for cat in outgoing:
        for item in cat['lists']:
            row=group(cat['target'],item['kind'],cat['class'])
            matches=[i for i,e in enumerate(row['entries']) if e['list']==item['list']]
            require(len(matches)==1,'retired outgoing category not attached exactly once')
            row['retired'].append({'list':item['list'],'image':cat['image'],'category':cat['name']})
            row['retired_positions']+=matches
    for cat in transfer['incoming']:
        require(cat['class'] in new_classes,'incoming category replacement class absent')
        cls=new_classes[cat['class']]['address'];address=int(cat['category_address'],16)
        recipe.pointer(address+8,cls,'incoming category target replacement class')
        for item in cat['lists']:
            row=group(cls,item['kind'],cat['class']);value=item['address']
            order=next(i for i,e in enumerate(item['source_entries']) if e['address']==value)
            row['incoming'].append({'list':value,'image':cat['image'],'image_index':final_indexes[cat['image']],
                                    'origin':'preserved_incoming','source_order':order,'category':cat['category']})
        records.append({'origin':'incoming','category':cat['category'],'image':cat['image'],'class':cat['class'],'target':cls})
    for cat in inventory['categories']:
        cls=recipe.reader.ptr(cat['address']+8)
        ro=recipe.reader.ptr(cls+32)&~7;name=recipe.reader.string(recipe.reader.ptr(ro+24))
        expected=cat['class_reference'].get('symbol')
        require(expected=='_OBJC_CLASS_$_'+name,'provider category target class identity differs')
        if cls not in new_owner and cls not in owners:
            owners.update(class_owners(original,{cls}))
        for item in cat['lists']:
            row=group(cls,item['kind'],name)
            row['provider_categories'].append({'list':item['address'],'image':cat['image'],
                'image_index':final_indexes[cat['image']],'origin':'provider_category','category':cat['name']})
        records.append({'origin':'provider','category':cat['name'],'image':cat['image'],'class':name,'target':cls})
    empty=None;emitted=[]
    for row in groups.values():
        retired={e['list'] for e in row['retired']}
        base=[e for e in row['entries'] if e['list'] not in retired]
        incoming=sorted(row['incoming'],key=lambda e:e['source_order'])
        provider=row['provider_categories']
        if row['class_address'] in new_owner:
            if not base and row['kind'].endswith('Methods'):
                if empty is None:empty=recipe.blob('category_lists',struct.pack('<II',3,0),'factory empty method-list sentinel')
                base=[{'list':empty,'image':row['base_owner'],'image_index':final_indexes[row['base_owner']],
                       'origin':'replacement_empty_base'}]
            entries=incoming+provider+base
        else:
            require(not incoming,'incoming category unexpectedly targets an unaffected class')
            if row['retired_positions']:
                before=min(row['retired_positions'])
                preserved_prefix=[e for e in row['entries'][:before] if e['list'] not in retired]
                preserved_suffix=[e for e in row['entries'][before:] if e['list'] not in retired]
                entries=preserved_prefix+provider+preserved_suffix
            else:entries=provider+base
        require(entries and len({e['list'] for e in entries})==len(entries),'empty/duplicate attached list')
        start=(recipe.cursors['category_lists']+7)&~7
        encoded=relative_list_blob(start,entries)
        address=recipe.blob('category_lists',encoded,'conditional attached '+row['class']+' '+row['kind'])
        require(address==start,'category blob allocation moved unexpectedly')
        recipe.pointer(row['ro_field'],address|1,'tagged preattached category list-list')
        emitted.append({k:v for k,v in row.items() if k not in ('incoming','provider_categories','entries')})
        emitted[-1].update({'address':address,'entries':entries})
    return {'categories':records,'groups':emitted,'retired_outgoing':outgoing,
            'original_index_fields_edited':[],'global_index_remap_owner':'objc_tables worker'}


def emit_recipe(family,original,layout,transfer,inventory,allocation,selectors,class_names,protocol_names,protocol_objects,final_order,protocol_class,method_types=None):
    reader=FamilyReader(family,allocation['selector_base']);recipe=Recipe(reader,allocation)
    indexes={name:i for i,name in enumerate(final_order)}
    require(len(indexes)==len(final_order) and 0<len(final_order)<65536 and
            set(final_order)<=set(layout['final_image_paths']),'final header index contract differs')
    method_edits(recipe,inventory,selectors,allocation['selector_base'],method_types)
    for ref in inventory['selector_refs']:
        require(reader.string(reader.ptr(ref['address']))==ref['selector'],'source selector reference semantic drift')
        recipe.pointer(ref['address'],selectors[ref['selector']],'unique selector reference')
    for row in inventory['classes']:
        for cls in (row['address'],row['metaclass']):
            ro=reader.ptr(cls+32)&~7
            require(reader.string(reader.ptr(ro+24))==row['name'],'provider class name drift')
            recipe.pointer(ro+24,class_names[row['name']],'canonical class name')
    new_classes={r['name']:r for r in inventory['classes']}
    retained=[]
    for ref in transfer['external_class_references']:
        if ref['replacement_export']['status']=='missing_symbol':
            # The replacement does not define the class (fold-in: Apple 27 Metal's _MTLTensorAuxiliaryPlane, absent
            # from OCLP's Metal wrapper and MetalOld). The pointer keeps the original class bytes, which stay in the
            # cache: what an Apple image holds today with Metal overridden on disk (8fb92d5). OPEN: whether anything
            # messages that class on this system.
            require(reader.ptr(int(ref['field'],16))==int(ref['target'],16),'retained external class reference differs')
            retained.append({k:ref[k] for k in ('image','kind','field','target','class','metaclass','source_image')})
            continue
        require(ref['replacement_export']['status']=='resolved' and ref['class'] in new_classes,
                'external class reference without replacement: '+ref['class'])
        target=new_classes[ref['class']]['metaclass' if ref['metaclass'] else 'address']
        require(reader.ptr(int(ref['field'],16))==int(ref['target'],16),'external class reference precondition differs')
        recipe.pointer(int(ref['field'],16),target,'retarget external '+ref['kind'])
    growth=growth_edits(recipe,inventory,transfer)
    canonical=canonical_protocol_edits(recipe,inventory,layout,protocol_objects,protocol_names,
        allocation['new_protocol']['address'],protocol_class)
    cats=category_edits(recipe,inventory,transfer,original,indexes,{r['path'] for r in layout['path_trie']['replacements']})
    for image in inventory['images']:
        if not image['image_info']:continue
        info=image['image_info'];flags=info['source_flags']|9
        recipe.byte(info['address']+4,struct.pack('<I',flags),'optimized image and complete preattached category flags')
    result=recipe.finish();result.update({'schema':1,'classification':'concrete checked ObjC byte/pointer/blob recipe; final integration and runtime acceptance not checked here',
        'category_transfer':cats,'ivar_growth':growth,'new_canonical_protocol':canonical,'retained_external_class_references':retained,
        'counts':{'provider_classes':len(inventory['classes']),'protocol_records':len(inventory['protocols']),
            'provider_method_lists':len(inventory['method_lists']),'small_lists':sum(r['small'] for r in inventory['method_lists']),
            'selector_references':len(inventory['selector_refs']),'incoming_categories':len(transfer['incoming']),
            'provider_categories':len(inventory['categories']),'external_class_references':len(transfer['external_class_references']),
            'retained_external_class_references':len(retained)},
        'finalization_requirements':['Apply byte/pointer edits to the pinned structural family before activating optimized flags.',
            'Pointer edits are decoded targets: rebuild chain membership including additions/removals and every new pointer field.',
            'Apply global old-to-final header indexes and selector/class/protocol/headerinfo tables from the coordinated global component.',
            'Regenerate loader ObjC records, identities, signatures and sidecars; retain required caches and existing security settings.']})
    return result
