import pathlib
import platform
import json
import importlib.util
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

@unittest.skipUnless(platform.system() == "Darwin", "requires the macOS Objective-C runtime")
class PolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="appstore-policy-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.output = pathlib.Path(cls.tmp.name)
        common = ["xcrun", "clang", "-arch", "x86_64", "-fobjc-arc", "-Wall", "-Wextra",
                  "-Werror", "-Wno-unused-parameter", "-framework", "Foundation"]
        for name, flags in [("fixture", []), ("wrong", ["-DWRONG_ABI"]),
                            ("wronggate", ["-DWRONG_GATE_ABI"])]:
            target = cls.output / (name + ".dylib")
            subprocess.run(common + flags + ["-dynamiclib", str(ROOT / "tests/fixture.m"),
                                             "-o", str(target)], check=True, capture_output=True)
            subprocess.run(["codesign", "-s", "-", str(target)], check=True, capture_output=True)
        cls.probe = cls.output / "probe"
        subprocess.run(common + ["-DM27_STORE_POLICY_TEST", str(ROOT / "src/store_policy.m"),
                                 str(ROOT / "probes/capability_trace.m"),
                                 str(ROOT / "tests/probe.m"), "-o", str(cls.probe)],
                       check=True, capture_output=True)
        subprocess.run(["codesign", "-s", "-", str(cls.probe)], check=True, capture_output=True)

    def run_probe(self, mode, fixture="fixture"):
        result = subprocess.run(["arch", "-x86_64", str(self.probe),
                                 str(self.output / (fixture + ".dylib")), mode],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_framework_loaded_after_policy(self):
        self.run_probe("late")

    def test_framework_already_loaded(self):
        self.run_probe("early")

    def test_wrong_daemon_abi_leaves_capability_unchanged(self):
        self.run_probe("wrong", "wrong")

    def test_absent_classes(self):
        self.run_probe("missing")

    def test_trace_refuses_wrong_argument_abi(self):
        self.run_probe("wronggate", "wronggate")

    def test_detached_signing_retains_bundle_seal(self):
        module_spec = importlib.util.spec_from_file_location("store_prepare", ROOT / "prepare.py")
        prepare = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(prepare)
        app = self.output / "Seal.app"
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        binary = contents / "MacOS/main"
        shutil.copy2(self.probe, binary)
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": "main", "CFBundleIdentifier": "org.m27.store-policy.test",
            "CFBundlePackageType": "APPL"}))
        ents = self.output / "ents.plist"
        ents.write_bytes(plistlib.dumps({"com.apple.security.cs.allow-jit": True}))
        subprocess.run(["codesign", "--force", "-s", "-", "--entitlements", str(ents), str(app)],
                       check=True, capture_output=True)
        original = self.output / "sealed-original"
        edited = self.output / "sealed-edited"
        shutil.copy2(binary, original)
        shutil.copy2(binary, edited)
        subprocess.run(["codesign", "--force", "-s", "-", "--preserve-metadata=identifier,entitlements,requirements,flags,runtime",
                        str(edited)], check=True, capture_output=True)

        prepare.preserve_bundle_slots(original, edited)
        shutil.copy2(edited, binary)
        subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True, capture_output=True)

        with (contents / "Info.plist").open("ab") as stream:
            stream.write(b"\n")
        result = subprocess.run(["codesign", "--verify", "--strict", str(app)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_preflight_machine_code_truth_tables(self):
        edits = json.loads((ROOT / "profiles/appstoreagent-26B131.json").read_text())["edits"]
        legacy = bytes.fromhex(edits[0]["replace"])[:12]
        daemon = json.loads((ROOT / "profiles/appstored-26B131.json").read_text())["edits"]
        self.assertEqual(legacy, bytes.fromhex(daemon[0]["replace"])[:12])
        media = bytes.fromhex(edits[1]["replace"])[6:9]
        assembly = self.output / "preflight.s"
        byte_directive = lambda b: ".byte " + ",".join(str(v) for v in b) + "\n"
        assembly.write_text(
            ".text\n.globl _legacy\n_legacy:\n"
            "pushq %r12\npushq %r14\nmovl %edi,%r12d\nmovl %esi,%r14d\n" +
            byte_directive(legacy) + "movl %esi,%eax\npopq %r14\npopq %r12\nret\n"
            ".globl _media\n_media:\nmovl %esi,%edx\nmovl %edi,%esi\n" +
            byte_directive(media) + "movl %esi,%eax\nret\n")
        source = self.output / "preflight.c"
        source.write_text('''
#include <assert.h>
extern int legacy(int, int), media(int, int);
int main(void) {
    for (int intel = 0; intel < 2; intel++)
        for (int arm = 0; arm < 2; arm++) {
            assert(legacy(intel, arm) == (intel || arm));
            assert(media(intel, arm) == (intel || arm));
        }
    return 0;
}
''')
        binary = self.output / "preflight"
        subprocess.run(["xcrun", "clang", "-arch", "x86_64", str(source), str(assembly),
                        "-o", str(binary)], check=True, capture_output=True)
        subprocess.run(["codesign", "-s", "-", str(binary)], check=True, capture_output=True)
        subprocess.run(["arch", "-x86_64", str(binary)], check=True, timeout=30)

if __name__ == "__main__":
    unittest.main()
