import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('copy_hooks', Path(__file__).resolve().parents[1]/'stages/copy-hooks.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CopyHooksTest(unittest.TestCase):
    def test_only_declared_input_stays_on_share(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root/'share', root/'local'
            hook = source/'02-test'
            (hook/'inputs/images').mkdir(parents=True)
            (hook/'inputs/native').mkdir()
            image = hook/'inputs/images/source.dmg'
            image.write_bytes(b'immutable image')
            stamp = image.stat().st_mtime_ns
            (hook/'inputs/native/kext').write_bytes(b'owned payload')
            (hook/'read-from-share.txt').write_text('# image input\nimages\n')
            (hook/'kc-hook.sh').write_text('recipe')
            module.copy_hooks(source, target)
            shared = target/'02-test/inputs/images'
            self.assertTrue(shared.is_symlink())
            self.assertEqual((shared/'source.dmg').read_bytes(), b'immutable image')
            self.assertEqual(image.stat().st_mtime_ns, stamp)
            self.assertFalse((target/'02-test/inputs/native').is_symlink())
            self.assertEqual((target/'02-test/inputs/native/kext').read_bytes(), b'owned payload')
            self.assertEqual((target/'02-test/kc-hook.sh').read_text(), 'recipe')


class PostAndDeclarations(unittest.TestCase):
    TOP = Path(__file__).resolve().parents[2]

    def test_post_recipes_are_copied_as_hooks_are(self):
        text = (self.TOP / 'pipeline/stages/golden-assemble.sh').read_text()
        self.assertIn('copy-hooks.py" "$G/post" "$W/post" --owner "$OWNER"', text)
        self.assertNotIn('(cd "$G/post" && tar', text)

    def test_the_big_read_only_inputs_are_read_in_the_kit(self):
        for decl, row in (('macos27tool-runtime/src', 'hao_runtime'), ('macos27tool-opencore', 'hao_runtime'),
                          ('golden-gate-icon-caches', 'icon_caches_golden_gate')):
            lines = [l.split('#', 1)[0].strip() for l in
                     (self.TOP / 'patches' / decl / 'read-from-share.txt').read_text().splitlines()]
            self.assertIn(row, lines, decl)

    def test_a_declaration_does_not_change_what_a_recipe_installs(self):
        import runpy, tempfile, shutil
        P = runpy.run_path(str(self.TOP / 'pipeline/patches.py'))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shutil.copytree(self.TOP / 'patches/golden-gate-icon-caches', root / 'golden-gate-icon-caches')
            before = P['recipe_digest']('golden-gate-icon-caches', str(root))
            (root / 'golden-gate-icon-caches/read-from-share.txt').write_text('other\n')
            self.assertEqual(before, P['recipe_digest']('golden-gate-icon-caches', str(root)))
