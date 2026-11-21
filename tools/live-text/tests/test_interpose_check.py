"""interpose-check: the interpose entries and calls through it reads from a runtime library (the cache side needs a
rebuilt family and ipsw; tools runs it on the B family)."""
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path

from common import UNIT, clang, run

loader = importlib.machinery.SourceFileLoader('interpose_check', str(UNIT/'interpose-check'))
spec = importlib.util.spec_from_loader('interpose_check', loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)

INTERPOSER = r'''
extern "C" {
int e5rt_hooked(void*) __attribute__((weak_import));
int e5rt_helper(void) __attribute__((weak_import));
int replacement_fn(void *p){return e5rt_helper()+e5rt_hooked(p);}
}
__attribute__((used,section("__DATA,__interpose"))) static const struct {const void *r,*o;} hook={(void*)replacement_fn,(void*)e5rt_hooked};
'''


class EntryTests(unittest.TestCase):
    def test_reads_entries_and_calls_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'interposer.mm'
            source.write_text(INTERPOSER)
            lib = clang(Path(tmp)/'libinterposer.dylib', '-x', 'c++', '-dynamiclib', '-undefined', 'dynamic_lookup',
                        source)
            pairs, called = check.entries(lib)
        self.assertEqual(pairs, [('_replacement_fn', '_e5rt_hooked')])
        self.assertEqual(called, {'_e5rt_hooked', '_e5rt_helper'})


class FlatImportTests(unittest.TestCase):
    def test_only_interpose_originals_are_flat(self):
        """A rebuilt cache cannot express flat lookups; the producer resolves only the e5rt names by name."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run('xcrun', 'clang', '-arch', 'x86_64', '-dynamiclib', UNIT/'src/config.c', '-DLIVE_TEXT_ROOT="/x"',
                '-o', out/'libLiveTextConfig.dylib')
            common = ('-std=c++17', '-fobjc-arc', '-fblocks', '-undefined', 'dynamic_lookup',
                      '-framework', 'Foundation', '-framework', 'IOSurface', '-framework', 'CoreFoundation',
                      '-Wl,-needed_library,' + str(out/'libLiveTextConfig.dylib'))
            cxx = lambda name, *args: run('xcrun', 'clang++', '-arch', 'x86_64', '-dynamiclib', *args, '-o', out/name)
            cxx('libm27_input_coherence.dylib', UNIT/'src/input_coherence.mm', *common)
            cxx('libLiveTextPolicy.dylib', UNIT/'src/policy.mm', *common,
                '-Wl,-needed_library,' + str(out/'libm27_input_coherence.dylib'))
            for name in ('libm27_input_coherence.dylib', 'libLiveTextPolicy.dylib'):
                imports = run('xcrun', 'dyld_info', '-imports', '-arch', 'x86_64', out/name).splitlines()
                flat = [line.split()[0] for line in imports if 'flat-namespace' in line]
                self.assertTrue(flat, name)
                self.assertEqual([s for s in flat if not s.startswith('_e5rt_')], [], name)


if __name__ == '__main__':
    unittest.main()
