import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import tempfile
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dyld_rebuild.exports import Refusal,span
from dyld_rebuild.objc_emit import Recipe,encode_small_methods,relative_list_blob,provider_inventory,check_small_method_types


class Memory:
    def __init__(self):self.data=bytearray(4096);self.slides={}
    def read(self,a,n):return span(self.data,a,n)
    def ptr(self,a):return struct.unpack('<Q',self.read(a,8))[0]
    def locate(self,a,n):return 0,4096,None,None


class ObjCEmitTests(unittest.TestCase):
    def method_row(self):
        return {'small':True,'count':2,'flags':0x8000000c,'address':0x1000,'methods':[
            {'selector':'later','types_address':0xa000,'imp':0x5000},
            {'selector':'earlier','types_address':0xa100,'imp':0x5100}]}
    def test_small_method_sort_recomputes_relative_fields(self):
        row=self.method_row();data,order=encode_small_methods(row,{'later':0x9000,'earlier':0x8000},0x7000)
        # the stock in-cache form 27's libobjc reads: types are offsets from the selector base, flag 0x20000000
        self.assertEqual(order,[1,0]);self.assertEqual(struct.unpack_from('<II',data),(0xe000000f,2))
        for index,source in enumerate(order):
            field=row['address']+8+index*12;name,types,imp=struct.unpack_from('<iii',data,8+index*12)
            self.assertEqual(0x7000+name,{'later':0x9000,'earlier':0x8000}[row['methods'][source]['selector']])
            self.assertEqual(0x7000+types,row['methods'][source]['types_address'])
            self.assertEqual(field+8+imp,row['methods'][source]['imp'])
    def test_signed_selector_and_imp_range_refusal(self):
        row=self.method_row()
        with self.assertRaises(Refusal):encode_small_methods(row,{'later':0x80000000,'earlier':1},0)
        row['methods'][0]['imp']=0x100000000
        with self.assertRaises(Refusal):encode_small_methods(row,{'later':2,'earlier':1},0)
    def test_types_below_the_selector_base_refused(self):
        row=self.method_row()
        with self.assertRaises(Refusal):encode_small_methods(row,{'later':0xb000,'earlier':0xb100},0xa080)
    def test_readback_decodes_types_from_the_selector_base(self):
        # a 4 KiB space: two type strings, a source list with entry-relative types, rewritten in place
        space=bytearray(0xc000);base=0x1000
        space[0xa000:0xa004]=b'v16\0';space[0xa100:0xa106]=b'@24@0\0'
        row=self.method_row();row['address']=0x2000
        before=bytearray(struct.pack('<II',0x8000000c,2))
        for i,m in enumerate(row['methods']):
            entry=0x2000+8+12*i;before+=struct.pack('<iii',0,m['types_address']-(entry+4),m['imp']-(entry+8))
        data,_=encode_small_methods(row,{'later':0x9000-0x4000,'earlier':0x8000-0x4000},base)
        space[0x2000:0x2000+len(data)]=data
        read=lambda a,n:bytes(space[a:a+n])
        self.assertEqual(check_small_method_types(read,base,0x2000,bytes(before)),[])
        # the old entry-relative form decodes to other bytes under 27's rule, and the stock-form check refuses it
        old=bytearray(before);struct.pack_into('<I',old,0,0xc000000f);space[0x2000:0x2000+len(old)]=old
        with self.assertRaises(Refusal):check_small_method_types(read,base,0x2000,bytes(before))
    def test_equal_selector_stable_order(self):
        row=self.method_row();row['methods'][1]['selector']='later'
        _,order=encode_small_methods(row,{'later':0x8000},0x7000);self.assertEqual(order,[0,1])
    def test_relative_list_signed48_and_index_order(self):
        entries=[{'list':0x1000,'image_index':3143},{'list':0x9000,'image_index':0}]
        blob=relative_list_blob(0x5000,entries);self.assertEqual(struct.unpack_from('<II',blob),(8,2))
        decoded=[]
        for i in range(2):
            word=struct.unpack_from('<Q',blob,8+i*8)[0];delta=word>>16
            if delta&(1<<47):delta-=1<<48
            decoded.append((0x5008+i*8+delta,word&65535))
        self.assertEqual(decoded,[(0x1000,3143),(0x9000,0)])
    def test_relative_list_overflow_and_empty_refused(self):
        for entries in ([],[{'list':1<<48,'image_index':0}],[{'list':0,'image_index':65536}]):
            with self.subTest(entries=entries),self.assertRaises(Refusal):relative_list_blob(0x1000,entries)
    def recipe(self):
        return Recipe(Memory(),{'category_lists':{'address':0x10000,'reserve':64},
            'new_protocol':{'address':0x20000,'reserve':96},'mappings':[
                {'address':0x10000,'size':4096,'maximum':1,'initial':1},
                {'address':0x20000,'size':4096,'maximum':3,'initial':3}]})
    def test_blob_reservation_and_protection_refusal(self):
        recipe=self.recipe();self.assertEqual(recipe.blob('new_protocol',bytes(96),'protocol'),0x20000)
        with self.assertRaises(Refusal):recipe.blob('new_protocol',b'x','overflow')
        recipe=self.recipe();recipe.allocation['mappings'][0]['initial']=3
        with self.assertRaises(Refusal):recipe.blob('category_lists',bytes(8),'wrong protection')
    def test_byte_conflict_and_pointer_overlap_refused(self):
        recipe=self.recipe();recipe.byte(16,b'abcd','first')
        with self.assertRaises(Refusal):recipe.byte(18,b'ef','overlap')
        recipe.pointer(16,0x1234,'pointer')
        with self.assertRaises(Refusal):recipe.finish()
    def test_decoded_pointer_addition_and_conflict(self):
        recipe=self.recipe();recipe.pointer(24,0x1000,'new field')
        edit=recipe.finish()['pointer_edits'][0]
        self.assertFalse(edit['member_before']);self.assertTrue(edit['member_after']);self.assertEqual(edit['expected'],0)
        with self.assertRaises(Refusal):recipe.pointer(24,0x2000,'conflict')
    def test_universal_input_checks_selected_slice_identity(self):
        selected=b"selected x86_64 metadata";container=b"other architecture"+selected
        offset=len(container)-len(selected)
        provider=SimpleNamespace(container_offset=offset,data=selected,reader=None,sections=[])
        row={'path':'/test','key':'test','input_path':'fat','input_sha256':hashlib.sha256(selected).hexdigest(),
             'input_size':len(selected),'slice_offset':offset,
             'placement':{'uniform_slide':0,'provider_header_address':4096}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'fat').write_bytes(container)
            with patch('dyld_rebuild.objc_emit.Provider',return_value=provider):
                result=provider_inventory({'providers':[row]},root)
                self.assertEqual(len(result['images']),1)
                for key,value in [('slice_offset',offset+1),('input_size',len(selected)+1),('input_sha256','0'*64)]:
                    damaged=dict(row);damaged[key]=value
                    with self.subTest(key=key),self.assertRaises(Refusal):
                        provider_inventory({'providers':[damaged]},root)

    def test_new_pointer_registration_omits_null(self):
        recipe=self.recipe();recipe.blob_pointer(0x20000,0,'nil');recipe.blob_pointer(0x20008,0x1234,'name')
        self.assertEqual(recipe.finish()['new_pointer_fields'],[{'address':0x20008,'target':0x1234,'reason':'name'}])


if __name__=='__main__':unittest.main()
