"""Golden host runner/config/completion fixtures; never start a VM or invoke APFS tools."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TOP=Path(__file__).resolve().parents[2]
RUN=runpy.run_path(str(TOP/'pipeline/golden-run'))


class CheckedRows(unittest.TestCase):
    def test_rows_handed_on_are_checked_copies(self):
        """The donor and hardware rows (SHARE/assembly-inputs), the OpenCore release and the QEMU reference tree reach
        their consumers as copies checked as copied (lock.py stage), never by a row's path after a check."""
        text=(TOP/'pipeline/golden-run').read_text()
        self.assertNotIn("lockrows['path']",text)
        self.assertIn("lockrows['stage'](str(lock_of(row)),row,str(extras/role),'ditto')",text)
        self.assertIn("'--opencore',checked_row(opencore_row)",text)
        self.assertIn("'--reference',checked_row(reference_row)",text)


class GoldenRun(unittest.TestCase):
    def test_capture_reads_stopped_workspace_readonly_and_detaches_on_failure(self):
        state=dict(vm='/fixture/vm',guest_local='/Volumes/work/job',
                   guest_workspace=dict(mount='/Volumes/work',image='/fixture/workspace.img'),
                   recipe=dict(size_bytes=4))
        calls=[]
        def command(args,**kwargs):
            calls.append(list(map(str,args)))
            if args[:2]==['hdiutil','attach']:
                root=Path(args[args.index('-mountpoint')+1]);(root/'job').mkdir()
                (root/'job/target.img').write_bytes(b'test')
        with mock.patch('runpy.run_path',return_value={'running':lambda vm:[]}):
            with self.assertRaisesRegex(RuntimeError,'fixture capture failure'):
                with RUN['readonly_workspace'](state,command) as image:
                    self.assertEqual(image.read_bytes(),b'test')
                    raise RuntimeError('fixture capture failure')
        self.assertIn('-readonly',calls[0]);self.assertIn('-nobrowse',calls[0])
        self.assertEqual(calls[-1][:2],['hdiutil','detach'])
        calls.clear()
        with mock.patch('runpy.run_path',return_value={'running':lambda vm:[123]}):
            with self.assertRaisesRegex(RuntimeError,'must be stopped'):
                with RUN['readonly_workspace'](state,command):pass
        self.assertEqual(calls,[])

    def share(self, folder, rows, hook_sh=()):
        share=Path(folder);(share/'kdk/KDK_25.kdk').mkdir(parents=True)
        (share/'patches.txt').write_text(''.join(stage+' '+name+' input\n' for stage,name in rows))
        for i,(stage,name) in enumerate(rows,1):
            path=share/('hooks' if stage=='oclp-hook' else 'post')/('%02d-%s'%(i,name));path.mkdir(parents=True)
            (path/('apply.sh' if stage=='post' else 'hook.sh' if name in hook_sh else 'kc-hook.sh')).touch()
        return share

    def test_from_overlays_config_and_system_check(self):
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),('post','post-one')]
        with tempfile.TemporaryDirectory() as folder:
            share=self.share(Path(folder)/'share',rows)
            recipe=dict(size_bytes=80,name='GG',golden_build='26B131',donor_build='25G342')
            self.assertNotIn('overlays',RUN['guest_config'](recipe,share,'/guest/job'))
            config=RUN['guest_config'](recipe,share,'/guest/job',overlays=True)
            self.assertEqual(config['overlays'],dict(plan='/private/var/ssdshare/overlay-plan.json',
                                                     files='/private/var/ssdshare/overlay-files',
                                                     installer='/guest/job/source/golden-overlays.py',
                                                     livepatch='/guest/job/source/livepatch.py'))
            # the host Golden Gate root must be the build the assembly makes
            import plistlib
            system=Path(folder)/'fs/System/Library/CoreServices';system.mkdir(parents=True)
            (system/'SystemVersion.plist').write_bytes(plistlib.dumps({'ProductBuildVersion':'26A428'}))
            with self.assertRaisesRegex(ValueError,'is build 26A428, the assembly builds 26B131'):
                RUN['prepare_overlays'](recipe,share,Path(folder),Path(folder),share,dict(system=Path(folder)/'fs',store=Path(folder)/'s'))
        for argv in (['golden-run','a.json','--repo','r','--from-overlays'],
                     ['golden-run','a.json','--repo','r','--overlay-system','/x']):
            with mock.patch.object(sys,'argv',argv),contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit):RUN['main']()
            self.assertIn('overlay',err.getvalue())

    def test_cryptex_cache_stages_matching_entries_and_keeps_new_ones(self):
        """prepare_cryptex_cache: key.json from the staged recipe, rows and donor tool; only entries with the same host
        lines are cloned into in/. collect_cryptex_cache: a new out/ entry is checked and moved into the store, a
        damaged one stops, and the store keeps the newest entries."""
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace')]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);share=self.share(root/'share',rows)
            staged=share/'hooks/02-golden-gate-userspace'
            for f in (TOP/'patches/golden-gate-userspace/src').glob('*.py'):shutil.copy2(f,staged/f.name)
            (share/'assembly-source/tahoe-donors').mkdir(parents=True);(share/'assembly-source/tahoe-donors/make').write_text('t')
            lock=root/'lock';lock.mkdir()
            for name in ('golden_gate_userspace','codeid_carrier','hao_runtime','tahoe_driverkit_donors'):
                (root/'store/artifacts'/name).mkdir(parents=True)
                (root/'store/artifacts'/name/'build.json').write_text('{"name": "%s"}'%name)
                (lock/name).write_text('- %s\n'%(root/'store/artifacts'/name))
            lockrows=runpy.run_path(str(TOP/'pipeline/lock.py'))
            store=root/'cache';recipe=dict(inputs=dict(driverkit='tahoe_driverkit_donors'))
            record=RUN['prepare_cryptex_cache'](recipe,share,store,lockrows,(lock,))
            key=json.loads((share/'cryptex-cache/key.json').read_text())
            self.assertEqual((record['host_key'],record['staged']),(key['host_key'],[]))
            self.assertTrue(any(l.startswith('source cryptex.py ') for l in key['host_lines']))
            self.assertFalse(any(' install.py ' in l for l in key['host_lines']))
            self.assertIn('codeid_carrier file build.json',' '.join(key['host_lines']))
            # the guest stored an entry: checked and moved into the store
            out=share/'cryptex-cache/out/k1';out.mkdir(parents=True)
            (out/'os.dmg').write_bytes(b'os');(out/'app.dmg').write_bytes(b'app')
            files={n:dict(sha256=RUN['digest'](out/n),size=len((out/n).read_bytes())) for n in ('os.dmg','app.dmg')}
            (out/'entry.json').write_text(json.dumps(dict(lines=key['host_lines']+['builder x'],files=files)))
            (share/'cryptex-cache/result.json').write_text(json.dumps(dict(key='k1',outcome='stored')))
            done=RUN['collect_cryptex_cache'](share,store,dict(record))
            self.assertEqual((done['stored']['key'],done['result']['outcome'],done['pruned']),('k1','stored',[]))
            self.assertEqual((store/'k1/os.dmg').read_bytes(),b'os')
            # the next build stages it; an entry of other inputs is not staged
            other=store/'k0';other.mkdir()
            (other/'entry.json').write_text(json.dumps(dict(lines=['schema other'],files={})))
            shutil.rmtree(share/'cryptex-cache')
            self.assertEqual(RUN['prepare_cryptex_cache'](recipe,share,store,lockrows,(lock,))['staged'],['k1'])
            self.assertEqual((share/'cryptex-cache/in/k1/os.dmg').read_bytes(),b'os')
            # a damaged new entry stops
            bad=share/'cryptex-cache/out/k2';bad.mkdir(parents=True)
            (bad/'os.dmg').write_bytes(b'xx')
            (bad/'entry.json').write_text(json.dumps(dict(files={'os.dmg':files['os.dmg']})))
            with self.assertRaisesRegex(RuntimeError,'the guest recorded'):RUN['collect_cryptex_cache'](share,store,{})
            shutil.rmtree(bad)
            # keep the newest: k1 was used last
            (share/'cryptex-cache/result.json').write_text(json.dumps(dict(key='k1',outcome='hit')))
            os.utime(other/'entry.json',(time.time()+60,)*2)
            self.assertEqual(RUN['collect_cryptex_cache'](share,store,{},keep=1)['pruned'],['k0'])
            self.assertEqual(sorted(p.name for p in store.iterdir()),['k1'])
            config=RUN['guest_config'](dict(size_bytes=80,name='GG'),share,'/guest/job',cryptex_cache=True)
            self.assertEqual(config['cryptex_cache'],'/private/var/ssdshare/cryptex-cache')
            self.assertNotIn('cryptex_cache',RUN['guest_config'](dict(size_bytes=80,name='GG'),share,'/guest/job'))

    def test_boot_efi_archive_carries_opencore_and_oclp_licenses(self):
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            prepared=Path(folder)/'boot-efi';(prepared/'payload/EFI/OC').mkdir(parents=True)
            (prepared/'payload/EFI/OC/OpenCore.efi').write_bytes(b'oc');(prepared/'manifest.json').write_text('{}')
            os.utime(prepared/'payload/EFI/OC/OpenCore.efi',(0,0))   # a release tar asset's mtime: written as 1980
            archive=RUN['boot_efi_archive'](prepared,Path(folder))
            with zipfile.ZipFile(archive) as bundle:
                names=set(bundle.namelist())
                self.assertIn('payload/EFI/OC/OpenCore.efi',names);self.assertIn('manifest.json',names)
                self.assertIn('Redistribution and use',bundle.read('LICENSES/OpenCorePkg-LICENSE.txt').decode())
                self.assertIn('Dortania',bundle.read('LICENSES/OpenCore-Legacy-Patcher-LICENSE.txt').decode())

    def test_a_recipe_with_seed_files_round_trips_to_its_install_manifest(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('golden_image_seed',TOP/'pipeline/stages/golden-image.py')
        golden=importlib.util.module_from_spec(spec);spec.loader.exec_module(golden)
        P=runpy.run_path(str(TOP/'pipeline/patches.py'))
        why='Metal appends to this shader cache on first GPU use'
        seed='Library/Application Support/PerUserSeed/metal/x/com.apple.metal/0_0/functions.data'
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);r=d/'patches'/'caches';r.mkdir(parents=True)
            (r/'patch.json').write_text(json.dumps({'revision':1,'stage':'post','pack':'base','files':[
                {'path':seed,'origin':{'kind':'hao'},'content':'seed','reason':why},
                {'path':'Library/plain','origin':{'kind':'hao'}}]}))
            self.assertEqual(P['file_problems'](r,json.loads((r/'patch.json').read_text())),[])
            share=d/'share';share.mkdir();(share/'patches.txt').write_text('post caches\n')
            with mock.patch.dict(RUN['declared_files'].__globals__,TOP=d):
                declared=RUN['declared_files'](share)
            self.assertEqual(declared['caches']['seed'],{seed:why})
            data=d/'data';(data/seed).parent.mkdir(parents=True);(data/seed).write_bytes(b'cache')
            (data/'Library/plain').write_bytes(b'p');(d/'declared.json').write_text(json.dumps(declared))
            class A:pass
            a=A();a.config={'declared':str(d/'declared.json')};a.mounts={'system':d/'system','data':data};a.manifests={}
            golden.Assembler.write_manifests(a)
            files=json.loads((data/golden.MANIFESTS/'caches.json').read_text())['files']
            self.assertEqual({k:(v.get('content'),v.get('reason')) for k,v in files.items()},
                             {seed:('seed',why),'Library/plain':(None,None)})

    def test_configuration_runs_every_recipe_in_set_order(self):
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),
              ('oclp-hook','later-fix'),('oclp-hook','selectors'),('post','post-one'),('post','post-two')]
        with tempfile.TemporaryDirectory() as folder:
            share=self.share(folder,rows)
            config=RUN['guest_config'](dict(size_bytes=80,name='GG',golden_build='26A428',donor_build='25G332'),share,'/guest/job')
            self.assertEqual(['darwin27-kernel','golden-gate-userspace','later-fix','selectors'],[h['name'] for h in config['hooks']])
            self.assertTrue(all(h['kc_hook'].endswith('%02d-%s/kc-hook.sh'%(i,h['name'])) for i,h in enumerate(config['hooks'],1)))
            self.assertEqual(['post-one','post-two'],[p['name'] for p in config['posts']])
            self.assertNotIn('kernel_hook',config);self.assertNotIn('userspace',config)
            self.assertTrue(config['kernel'].endswith('01-darwin27-kernel/inputs/darwin27_kernel'))
            self.assertTrue(config['payload'].endswith('02-golden-gate-userspace/inputs/golden_gate_userspace'))
            self.assertIn('/assembly-inputs/native',config['donors']['native'])
            self.assertEqual('/guest/job/tmp',config['environment']['TMPDIR'])
            self.assertEqual('golden-pristine.sh',Path(config['pristine_script']).name)

    def test_hooks_get_the_target_model_and_its_platform_data(self):
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),('post','post-one')]
        with tempfile.TemporaryDirectory() as folder:
            share=self.share(folder,rows)
            recipe=dict(size_bytes=80,name='GG',golden_build='26B131',donor_build='25G342',target='mbp92')
            RUN['stage_target'](recipe,share)
            config=RUN['guest_config'](recipe,share,'/guest/job')
            self.assertEqual('MacBookPro9,2',config['environment']['HAO_TARGET_MODEL'])
            self.assertEqual(RUN['GUEST']+'/target-data.json',config['environment']['HAO_TARGET_DATA'])
            # mbp92 has no platform_data yet: hooks read {} (and see nothing else new)
            target=json.loads((TOP/'pipeline/targets/mbp92.json').read_text())
            self.assertEqual(target.get('platform_data',{}),json.loads((share/'target-data.json').read_text()))
            plain=RUN['guest_config']({k:v for k,v in recipe.items() if k!='target'},share,'/guest/job')
            self.assertEqual({k:v for k,v in config['environment'].items() if not k.startswith('HAO_TARGET_')},plain['environment'])
            self.assertEqual({k:v for k,v in config.items() if k!='environment'},{k:v for k,v in plain.items() if k!='environment'})

    def test_target_data_of_a_fixture_target(self):
        with tempfile.TemporaryDirectory() as folder:
            targets=Path(folder)/'pipeline/targets';targets.mkdir(parents=True)
            (targets/'gen.json').write_text(json.dumps({'model':'iMac20,1','platform_data':{'model_gates':{'renamed':'X'}}}))
            (targets/'bad.json').write_text(json.dumps({'model':'iMac20,1','platform_data':[1]}))
            share=Path(folder)/'share';share.mkdir()
            with mock.patch.dict(RUN['stage_target'].__globals__,TOP=Path(folder)):
                RUN['stage_target'](dict(target='gen'),share)
                self.assertEqual({'model_gates':{'renamed':'X'}},json.loads((share/'target-data.json').read_text()))
                self.assertEqual('iMac20,1',RUN['target_environment'](dict(target='gen'),'/k')['HAO_TARGET_MODEL'])
                with self.assertRaises(ValueError):RUN['stage_target'](dict(target='bad'),share)

    def mbp92_configs(self):
        """The fixture share of fixtures/golden-run/mbp92-guest-config.json, configured as there."""
        recipe=json.loads((TOP/'pipeline/assemblies/mbp92-26B131-alpha-v0.1.0.json').read_text())
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),('oclp-hook','later-fix'),('post','post-one')]
        kit=runpy.run_path(str(TOP/'pipeline/kit.py'))
        out={}
        with tempfile.TemporaryDirectory() as folder:
            share=self.share(folder,rows)
            for name,kw in (('plain',{}),('cryptex',dict(cryptex_cache=True)),('overlays',dict(overlays=True,cryptex_cache=True))):
                c=RUN['guest_config'](recipe,share,'/Volumes/builder-work-X/job',**kw)
                out[name]=dict(config=c,kit=kit['template'](c,'mbp92-26B131-alpha-v0.1.0',RUN['GUEST'],'/Volumes/builder-work-X/job'))
        return out

    def test_the_9_2_guest_config_is_unchanged(self):
        # The fixture was written by golden-run before targets without an OCLP hardware overlay existed: the 9,2's
        # guest configuration and kit.json stay byte-identical.
        want=(TOP/'pipeline/tests/fixtures/golden-run/mbp92-guest-config.json').read_text()
        got=self.mbp92_configs()
        self.assertEqual(json.dumps(got,indent=1,sort_keys=True)+'\n',want)
        self.assertEqual(got['plain']['config']['hardware']['tool'],'/Volumes/builder-work-X/job/source/hardware/apply')

    def test_the_generic_target_has_no_hardware_overlay_and_no_esp_row(self):
        recipe=json.loads((TOP/'pipeline/assemblies/generic-26B131.json').read_text())
        self.assertNotIn('hardware',recipe['inputs'])
        self.assertNotIn('esp',json.loads((TOP/'pipeline/targets/generic.json').read_text()))
        self.assertFalse(RUN['hardware_overlay'](recipe))
        nine_recipe=json.loads((TOP/'pipeline/assemblies/mbp92-26B131-alpha-v0.1.0.json').read_text())
        rows=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),('post','opencore-generic'),('post','macos27tool-opencore')]
        with tempfile.TemporaryDirectory() as folder:
            share=self.share(folder,rows)
            config=RUN['guest_config'](recipe,share,'/guest/job',cryptex_cache=True)
            nine=RUN['guest_config'](nine_recipe,share,'/guest/job',cryptex_cache=True)
        self.assertIsNone(config['hardware'])
        self.assertEqual('iMac20,1',config['environment']['HAO_TARGET_MODEL'])
        self.assertEqual(config['esp'],'/guest/job/source/esp')     # the ESP build-efi.py makes, not a harvested tar
        self.assertFalse([v for v in runpy.run_path(str(TOP/'pipeline/kit.py'))['strings'](config)
                          if 'assembly-inputs/hardware' in v or 'pristine-ESP' in v])
        # everything else as the 9,2 has it
        self.assertEqual({k:v for k,v in config.items() if k not in ('hardware','environment')},
                         {k:v for k,v in nine.items() if k not in ('hardware','environment')})

    def test_a_hardware_input_must_match_the_targets_patch_sets(self):
        nine=json.loads((TOP/'pipeline/assemblies/mbp92-26B131-alpha-v0.1.0.json').read_text())
        self.assertTrue(RUN['hardware_overlay'](nine))
        self.assertTrue(RUN['hardware_overlay']({}))                 # no target (fixtures): as before
        without=dict(nine,inputs={k:v for k,v in nine['inputs'].items() if k!='hardware'})
        with self.assertRaisesRegex(ValueError,'names no "hardware" input'):RUN['hardware_overlay'](without)
        generic=json.loads((TOP/'pipeline/assemblies/generic-26B131.json').read_text())
        extra=dict(generic,inputs=dict(generic['inputs'],hardware='oclp_hardware'))
        with self.assertRaisesRegex(ValueError,'selects no OCLP patch sets.*oclp_hardware'):RUN['hardware_overlay'](extra)

    def test_the_esp_comes_from_the_targets_efi_build_only(self):
        nine=json.loads((TOP/'pipeline/assemblies/mbp92-26B131-alpha-v0.1.0.json').read_text())
        self.assertEqual(RUN['efi_build_of'](nine),json.loads((TOP/'pipeline/targets/mbp92.json').read_text())['efi_build'])
        build=runpy.run_path(str(TOP/'pipeline/stages/build-efi.py'))
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'efi-less.json';target.write_text('{"model": "iMac20,1"}')
            with self.assertRaisesRegex(ValueError,'efi-less has no "efi_build"'):
                build['build'](folder,None,target,'fd83bda09',Path(folder)/'OpenCore-1.0.5-RELEASE.zip',Path(folder)/'esp')
        # The generic target's ESP is the qemu_reference_efi tree with the pinned OpenCore release, not OCLP's build.
        generic=json.loads((TOP/'pipeline/assemblies/generic-26B131.json').read_text())
        self.assertEqual(RUN['efi_build_of'](generic)['base'],'qemu_reference')
        self.assertNotIn('base',RUN['efi_build_of'](nine))             # the 9,2: OCLP's build, as before
        with tempfile.TemporaryDirectory() as folder:
            targets=Path(folder)/'pipeline/targets';targets.mkdir(parents=True)
            (targets/'odd.json').write_text('{"model": "iMac20,1", "efi_build": {"base": "harvested"}}')
            (Path(folder)/'pipeline/stages').mkdir()
            shutil.copy2(TOP/'pipeline/stages/build-efi.py',Path(folder)/'pipeline/stages/build-efi.py')
            with mock.patch.dict(RUN['efi_build_of'].__globals__,TOP=Path(folder)):
                with self.assertRaisesRegex(ValueError,"odd: unknown \"efi_build\" base 'harvested'"):
                    RUN['efi_build_of'](dict(target='odd'))

    def test_skipped_optional_groups_merge_assembly_and_flag(self):
        self.assertEqual(RUN['skipped_groups']({}),[])
        self.assertEqual(RUN['skipped_groups']({'skip_optional':['caches']},['caches','other']),['caches','other'])
        for bad in ('caches',[''],[1]):
            with self.assertRaises(ValueError):RUN['skipped_groups']({'skip_optional':bad})

    def test_no_build_wide_dev_switch(self):
        # A dev image differs from an end-user image only by its dev recipes (pack dev); no environment switch
        # lets another recipe add dev-only content.
        base=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace'),('post','macos27tool-opencore')]
        for rows in (base,base+[('post','dev-access')]):
            with tempfile.TemporaryDirectory() as folder:
                config=RUN['guest_config'](dict(size_bytes=80,name='GG'),self.share(folder,rows),'/guest/job')
                self.assertNotIn('IMAGE_DEV_BUILD',config['environment'])

    def test_set_order_must_be_run_order(self):
        base=[('oclp-hook','darwin27-kernel'),('oclp-hook','golden-gate-userspace')]
        cases=[([('oclp-hook','golden-gate-userspace'),('oclp-hook','darwin27-kernel')],(),'must start with darwin27-kernel then golden-gate-userspace'),
               ([('oclp-hook','fix')]+base,(),'must start with darwin27-kernel then golden-gate-userspace'),
               (base+[('oclp-hook','selectors')],('selectors',),'selectors: src/hook.sh'),
               (base+[('post','opencore'),('oclp-hook','late-fix')],(),'post recipe precedes an oclp-hook')]
        for rows,hook_sh,message in cases:
            with self.subTest(message=message),tempfile.TemporaryDirectory() as folder:
                share=self.share(folder,rows,hook_sh)
                with self.assertRaisesRegex(ValueError,message):
                    RUN['guest_config'](dict(size_bytes=80,name='GG'),share,'/guest/job')

    def test_shipped_golden_sets_are_in_run_order(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('patches_module',TOP/'pipeline/patches.py')
        patches=importlib.util.module_from_spec(spec);spec.loader.exec_module(patches)
        for name in ('golden-gate-26B131','dev-bs-test','dev-golden-bootstrap','dev-metal27'):
            rows=[(stage,patch) for stage,patch,_ in patches.plan(str(TOP/'pipeline/sets'/(name+'.txt')),
                                                                  str(TOP/'pipeline/targets/mbp92.json'),'26B131/25G342')]
            with self.subTest(set=name),tempfile.TemporaryDirectory() as folder:
                share=self.share(folder,rows)
                for stage,patch in rows:
                    if stage=='oclp-hook':
                        self.assertTrue((TOP/'patches'/patch/'src/kc-hook.sh').is_file(),patch)
                        self.assertFalse((TOP/'patches'/patch/'src/hook.sh').exists(),patch)
                RUN['set_order'](share)

    def test_release_26B131_set_is_the_dev_set_without_dev_or_diagnostic_recipes(self):
        def names(name):
            return [l.split('#',1)[0].strip() for l in (TOP/'pipeline/sets'/(name+'.txt')).read_text().splitlines()
                    if l.split('#',1)[0].strip()]
        def dev(name):
            return json.loads((TOP/'patches'/name/'patch.json').read_text()).get('status') in ('dev','diagnostic')
        self.assertEqual([n for n in names('dev-bs-test') if not dev(n)],names('golden-gate-26B131'))
        assembly=json.loads((TOP/'pipeline/assemblies/mbp92-26B131.json').read_text())
        developer=json.loads((TOP/'pipeline/assemblies/dev-26B131.json').read_text())
        self.assertEqual('golden-gate-26B131',assembly.pop('patch_set'));developer.pop('patch_set')
        assembly.pop('repo',None);developer.pop('repo',None)   # each keeps its own image repository
        self.assertEqual(developer,assembly)

    def test_metal27_set_is_the_dev_set_plus_its_one_recipe(self):
        def names(name):
            return [l.split('#',1)[0].strip() for l in (TOP/'pipeline/sets'/(name+'.txt')).read_text().splitlines()
                    if l.split('#',1)[0].strip()]
        dev=names('dev-bs-test');at=dev.index('golden-gate-live-text-shared')+1
        self.assertEqual(dev[:at]+['golden-gate-metal27']+dev[at:],names('dev-metal27'))
        self.assertEqual('golden-gate-native-identities',dev[at])
        for name in ('dev-bs-test','golden-gate-26B131','golden-gate'):
            self.assertNotIn('golden-gate-metal27',names(name))
        variant=json.loads((TOP/'pipeline/assemblies/dev-26B131-metal27.json').read_text())
        developer=json.loads((TOP/'pipeline/assemblies/dev-26B131.json').read_text())
        self.assertEqual('dev-metal27',variant.pop('patch_set'));developer.pop('patch_set')
        self.assertNotEqual(variant.pop('repo'),developer.pop('repo'))
        self.assertEqual(developer,variant)

    def fixture_job(self, root, rc, workspace=None):
        share=root/'share';source=share/'assembly-source';source.mkdir(parents=True)
        for name in ('post','hooks','site','py/bin'):(share/name).mkdir(parents=True)
        python=share/'py/bin/python3.12';python.write_text('#!/bin/sh\nexec '+sys.executable+' "$@"\n');python.chmod(0o755)
        shutil.copy2(TOP/'pipeline/stages/copy-hooks.py',source/'copy-hooks.py')
        shutil.copy2(TOP/'pipeline/stages/golden-assemble.sh',source/'golden-assemble.sh')
        shutil.copy2(TOP/'pipeline/kit.py',source/'kit.py')
        if not (share/'py-x86_64').exists():(share/'py-x86_64').symlink_to('py')
        script='''import json,pathlib,sys
c=json.load(open(sys.argv[1]));w=pathlib.Path(c['work']);w.mkdir()
(w/'command-000.log').write_text('retained command evidence')
(w/'build.json').write_text(json.dumps(dict(status='assembled' if c['rc']==0 else 'failed',attached_device=None)))
print('RESULT rc=0',flush=True)
if c['rc']==0:pathlib.Path(c['image']).write_bytes(b'regular raw image fixture')
raise SystemExit(c['rc'])
'''
        (source/'golden-image.py').write_text(script)
        local=root/'guest-local'
        (share/'kit.json').write_text(json.dumps(RUN['KIT']['template'](dict(work=str(local/'work'),rc=rc),'fixture',
                                                                        str(share),str(local))))
        text=RUN['job_script'](str(local),str(share),'%d:%d'%(os.getuid(),os.getgid()),workspace=workspace)
        environment=dict(os.environ);environment.pop('SSDVM_AGENT_ID',None)
        result=subprocess.run(['/bin/sh'],input=text,text=True,capture_output=True,env=environment)
        self.assertEqual(0,result.returncode,result.stderr)
        return share/'out-golden'

    def test_formatter_failure_stops_before_local_copy_or_image_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            output=self.fixture_job(root,0,dict(size_bytes=160*1024**3,mount='/Volumes/not-mounted-by-fixture',name='fixture'))
            self.assertEqual('1',(output/'result').read_text().strip())
            self.assertIn('refusing host execution',(output/'golden.log').read_text())
            self.assertFalse((root/'guest-local').exists())
            self.assertFalse((output/'target.img').exists())

    def test_host_workspace_creation_is_exclusive_sparse_regular_file(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'workspace.img'
            RUN['create_workspace_image'](image,160*1024**3)
            self.assertEqual(160*1024**3,image.stat().st_size)
            self.assertEqual(0,image.stat().st_blocks)
            with self.assertRaises(FileExistsError):RUN['create_workspace_image'](image,1)
            self.assertEqual(160*1024**3,image.stat().st_size)

    def test_actual_shell_job_exports_only_after_success_and_retains_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            output=self.fixture_job(Path(folder),0)
            self.assertEqual('0', (output/'result').read_text().strip())
            self.assertEqual(b'regular raw image fixture',(output/'target.img').read_bytes())
            self.assertEqual('retained command evidence',(output/'guest/command-000.log').read_text())
            self.assertEqual('assembled',RUN['assembled'](output)['status'])

    def test_actual_shell_failure_retains_logs_and_does_not_export_image(self):
        with tempfile.TemporaryDirectory() as folder:
            output=self.fixture_job(Path(folder),7)
            self.assertEqual('7',(output/'result').read_text().strip())
            self.assertFalse((output/'target.img').exists())
            self.assertIn('RESULT rc=0',(output/'golden.log').read_text())
            self.assertTrue((output/'guest/build.json').is_file())
            with self.assertRaises(RuntimeError):RUN['assembled'](output)

    def test_nested_result_does_not_authorize_early_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder);(output/'golden.log').write_text('RESULT rc=0\n')
            def complete():
                time.sleep(0.05);(output/'result.new').write_text('9\n'); (output/'result.new').replace(output/'result')
            thread=threading.Thread(target=complete);thread.start()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(9,RUN['wait_job'](output,2,poll=0.005))
            thread.join()

    def test_attached_or_failed_record_cannot_authorize_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder);(output/'guest').mkdir()
            for status,device in [('failed',None),('assembled','/dev/disk99')]:
                (output/'guest/build.json').write_text(json.dumps(dict(status=status,attached_device=device)))
                with self.assertRaises(RuntimeError):RUN['assembled'](output)

    def test_lock_is_exclusive_and_removed_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            lock=Path(folder)/'builder.lock'
            with self.assertRaisesRegex(RuntimeError,'fixture failure'):
                with RUN['builder_lock'](lock,'golden fixture'):
                    self.assertEqual('golden fixture\n',(lock/'owner').read_text())
                    with self.assertRaises(FileExistsError):
                        with RUN['builder_lock'](lock,'other'):pass
                    raise RuntimeError('fixture failure')
            self.assertFalse(lock.exists())


if __name__=='__main__':unittest.main()
