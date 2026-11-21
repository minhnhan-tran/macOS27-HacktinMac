import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dyld_rebuild.exports import Refusal
from dyld_rebuild.witness_runtime import policy_profile,link
from dyld_rebuild.x86 import Disassembler


class WitnessRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.symbols={
            '28getLibPrespecializedMetadataEP':0x1000,'end1':0x1040,
            '28libPrespecializedImageLoadedEv':0x2000,'end2':0x2020,
            '28getLibPrespecializedMetadataEP.cold.1':0x3000,'end3':0x3020,
            '__ZL17LibPrespecialized':0x5000,
            'LazyI22LibPrespecializedStateE19defaultInitCallback':0x4000}
        self.data=bytearray(b'\x90'*0x6000)
        def rip(address,prefix,target,suffix=b''):
            instruction=prefix+struct.pack('<i',target-address-len(prefix)-4-len(suffix))+suffix
            self.data[address:address+len(instruction)]=instruction
        rip(0x1000,b'\x48\x83\x3d',0x5038,b'\xff')
        rip(0x1008,b'\x8b\x05',0x5008)
        rip(0x100e,b'\xe8',0x3000)
        rip(0x2000,b'\xc7\x05',0x5008,b'\x04\0\0\0')
        for address,prefix,target in ((0x3000,b'\x48\x8d\x3d',0x5038),
                                     (0x3007,b'\x48\x8d\x35',0x5000),
                                     (0x300e,b'\x48\x8d\x15',0x4000)):
            rip(address,prefix,target)

    def profile(self):
        dis=Disassembler()
        try:return policy_profile(lambda a,n:self.data[a:a+n],self.symbols,dis)
        finally:dis.close()

    def test_policy_state_is_derived_from_the_runtime_that_reads_and_disables_it(self):
        p=self.profile()
        self.assertEqual((p['once'],p['mode'],p['initialize']),(0x5038,0x5008,0x3000))
        # Changing the callback alone must not select a different writable word.
        self.data[0x2002:0x2006]=struct.pack('<i',0x5010-0x200a)
        with self.assertRaisesRegex(Refusal,'does not read'):self.profile()

    def test_initializer_cannot_target_a_different_lazy_state(self):
        self.data[0x300a:0x300e]=struct.pack('<i',0x5010-0x300e)
        with self.assertRaisesRegex(Refusal,'initializer arguments'):self.profile()

    @unittest.skipUnless(os.environ.get('SWIFT_WITNESS_RUNTIME_TOOLCHAIN'),'set SWIFT_WITNESS_RUNTIME_TOOLCHAIN for native generator tests')
    def test_real_relocator_preserves_slide_and_real_linker_resolves_configuration(self):
        toolchain=Path(os.environ['SWIFT_WITNESS_RUNTIME_TOOLCHAIN']).resolve()
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);source=out/'entry.bin'
            # The RIP-relative load exercises more than just a push/mov prefix.
            source.write_bytes(b'\x48\x8b\x05'+struct.pack('<i',0x1ff9)+b'\x90'*64)
            for shift,suffix in ((0,''),(0x124000,'-slid')):
                r=subprocess.run([str(toolchain/'relocate'),str(source),hex(0x100000+shift),hex(0x110000+shift),
                    hex(0x111000+shift),str(out/('patch'+suffix)),str(out/('trampoline'+suffix))],capture_output=True,text=True,check=True)
                self.assertEqual(json.loads(r.stdout)['displaced'],7)
            for name in ('patch','trampoline'):
                self.assertEqual((out/name).read_bytes(),(out/(name+'-slid')).read_bytes())
            dis=Disassembler()
            try:
                moved,_=dis.interval((out/'trampoline').read_bytes(),0x111000)
                self.assertEqual(moved[0].rip_target,0x102000)
                self.assertEqual(moved[-1].direct_target,0x100007)
                self.assertEqual(dis.decode((out/'patch').read_bytes(),0x100000).direct_target,0x110000)
            finally:dis.close()
            blob,symbols,_=link(toolchain,0x100000,0x110000,out)
            entry=symbols['_m27_witness_lookup']
            self.assertLess(0x100000,entry);self.assertLess(entry,0x100000+len(blob))

    @unittest.skipUnless(os.environ.get('SWIFT_WITNESS_RUNTIME_TOOLCHAIN'),'set SWIFT_WITNESS_RUNTIME_TOOLCHAIN for native policy tests')
    def test_compiled_runtime_initialization_hit_miss_and_all_disabled_modes(self):
        toolchain=Path(os.environ['SWIFT_WITNESS_RUNTIME_TOOLCHAIN']).resolve()
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);source=out/'test.c';binary=out/'test'
            source.write_text(r'''
#include "swift-witness-runtime.h"
#include <assert.h>
static intptr_t once;
static uint32_t mode;
static unsigned initialized,lookups,misses;
static int hit,miss,descriptor,type,argument,metatypeHit,metatypeKey,normalizedHit,conformance,otherConformance;
static const void *exactMetadata;
static const void *arguments[]={&argument};
static void initialize(void) { initialized++;once=-1;mode=2; }
static const void *lookup(const void *table,const void *key,size_t count,const void **rest) {
  lookups++;
  if(table==&normalizedHit) {
    assert(key==&descriptor && count==2);
    return rest[0]==&type && rest[1]==&conformance ? &normalizedHit : 0;
  }
  assert(count==1);
  if(table==&metatypeHit) {
    assert(key==&metatypeKey);
    return rest[0]==&type ? &metatypeHit : 0;
  }
  assert(table==&hit && key==&descriptor);
  return rest[0]==&type ? &hit : 0;
}
static const void *original(const void *key,const void *value,const void *const *args) {
  misses++;assert(key==&descriptor && value && args==arguments);return &miss;
}
static const void *originalMetatype(const void *value) {
  misses++;assert(value);return &miss;
}
static const void *originalMetadata(const void *key,const void *const *args) {
  (void)key;(void)args;misses++;return exactMetadata;
}
int main(void) {
  m27_witness_runtime=(struct WitnessRuntime){&once,initialize,&mode,&hit,lookup,original};
  assert(m27_witness_lookup(&descriptor,&type,arguments)==&hit);
  assert(initialized==1 && lookups==1 && misses==0);
  assert(m27_witness_lookup(&descriptor,&argument,arguments)==&miss);
  assert(initialized==1 && lookups==2 && misses==1);
  const uint32_t disabled[]={0,1,3,4};
  for(unsigned i=0;i<4;i++) { mode=disabled[i];assert(m27_witness_lookup(&descriptor,&type,arguments)==&miss); }
  assert(initialized==1 && lookups==2 && misses==5);
  m27_metatype_runtime=(struct MetatypeRuntime){&once,initialize,&mode,&metatypeHit,lookup,&metatypeKey,originalMetatype};
  once=0;
  assert(m27_metatype_lookup(&type)==&metatypeHit);
  assert(initialized==2 && lookups==3 && misses==5);
  assert(m27_metatype_lookup(&argument)==&miss);
  assert(initialized==2 && lookups==4 && misses==6);
  for(unsigned i=0;i<4;i++) { mode=disabled[i];assert(m27_metatype_lookup(&type)==&miss); }
  assert(initialized==2 && lookups==4 && misses==10);
  const void *witnessA[]={&conformance},*witnessB[]={&conformance},*witnessOther[]={&otherConformance};
  const void *metadataArgs[]={&type,witnessA};
  const struct MetadataShape shapes[]={{&descriptor,1,2}};
  m27_metadata_runtime=(struct MetadataRuntime){&mode,&normalizedHit,lookup,originalMetadata,shapes,1};
  mode=2;
  assert(m27_metadata_lookup(&descriptor,metadataArgs)==&normalizedHit);
  metadataArgs[1]=witnessB;
  assert(m27_metadata_lookup(&descriptor,metadataArgs)==&normalizedHit);
  metadataArgs[1]=witnessOther;
  assert(m27_metadata_lookup(&descriptor,metadataArgs)==0);
  assert(m27_metadata_lookup(&argument,metadataArgs)==0);
  unsigned prior=lookups;
  exactMetadata=&hit;
  assert(m27_metadata_lookup(&descriptor,metadataArgs)==&hit);
  assert(lookups==prior);
  exactMetadata=0;metadataArgs[1]=witnessA;
  for(unsigned i=0;i<4;i++) { mode=disabled[i];assert(m27_metadata_lookup(&descriptor,metadataArgs)==0); }
  assert(lookups==prior);
  return 0;
}
''')
            subprocess.run(['xcrun','clang','-arch','x86_64','-Wall','-Wextra','-Werror','-I',str(ROOT/'tools'),
                            str(source),str(toolchain/'runtime.o'),'-o',str(binary)],check=True,capture_output=True)
            subprocess.run(['codesign','-s','-',str(binary)],check=True,capture_output=True)
            subprocess.run([str(binary)],check=True,timeout=15)

    @unittest.skipUnless(os.environ.get('SWIFT_RUNTIME_TEST_CACHE') and os.environ.get('SWIFT_WITNESS_RUNTIME_TOOLCHAIN'),
                         'set target cache and rebuilt runtime toolchain for installation binding')
    def test_target_metatype_entry_and_distinct_table_are_relocated_without_mutating_cache(self):
        from dyld_rebuild.loader_generation import LoaderFamily
        from dyld_rebuild.witness_runtime import profile, recipe
        family=LoaderFamily(Path(os.environ['SWIFT_RUNTIME_TEST_CACHE']))
        try:
            p=profile(family)
            address=(p['entry']+0x100000+4095)&~4095
            layout={name:dict(address=address+offset,reserve=8192 if name in ('swift_runtime_code','swift_runtime_data') else 4096)
                    for name,offset in (('swift_runtime_code',0),('swift_runtime_data',0x2000),
                                        ('swift_witness_table',0x4000),('swift_metatype_table',0x5000),('swift_normalized_table',0x6000))}
            with tempfile.TemporaryDirectory() as temp:
                result=recipe(family,Path(os.environ['SWIFT_WITNESS_RUNTIME_TOOLCHAIN']),layout,
                              Path(temp)/'recipe',metatypes=True,metadata_shapes={0x1000:(1,2)})
                self.assertEqual(set(result['families']),{'witness','metatype','metadata'})
                self.assertEqual({e['address'] for e in result['byte_edits']},{p['entry'],p['metatype_entry'],p['metadata_lookup']})
                targets={r['target'] for r in result['new_pointer_fields']}
                self.assertIn(layout['swift_metatype_table']['address'],targets)
                self.assertIn(p['metatype_entry'],targets)
                self.assertEqual(len(result['new_pointer_fields']),19)
                data=bytes.fromhex(result['blobs'][1]['data'])
                self.assertEqual(struct.unpack_from('<II',data,4096+8),(1,2))
                self.assertIn(p['base']+0x1000,targets)
                for edit in result['byte_edits']:
                    before=bytes.fromhex(edit['before'])
                    self.assertEqual(family.read(edit['address'],len(before)),before)
        finally:
            family.close()


if __name__=='__main__':unittest.main()
