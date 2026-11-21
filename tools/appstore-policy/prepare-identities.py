import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SUPPORT = "System/Library/PrivateFrameworks/AppStoreDaemon.framework/Support/"
UI = "System/Applications/App Store.app/Contents/MacOS/App Store"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def cdhash(path):
    result = subprocess.run(["codesign", "-d", "--verbose=4", "--arch", "x86_64", str(path)],
                            capture_output=True, text=True, check=True)
    match = re.search(r"^CDHash=([0-9a-f]{40})$", result.stderr, re.MULTILINE)
    if not match:
        raise ValueError(f"no x86_64 CDHash: {path.name}")
    return match[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--flat-originals", action="store_true")
    for name in ("agent-original", "daemon-original", "ui-original", "stub-list", "native-list"):
        parser.add_argument("--" + name, required=True, type=pathlib.Path)
    args = parser.parse_args()
    record = json.loads((args.candidate / "build.json").read_text())
    originals = {"appstoreagent": args.agent_original, "appstored": args.daemon_original,
                 "app-store-ui": args.ui_original}
    for name, path in originals.items():
        if sha(path) != record["input_sha256"][name]:
            parser.error(f"original input changed: {name}")
    for name, expected in record["files"].items():
        if sha(args.candidate / name) != expected:
            parser.error(f"candidate changed: {name}")
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "store").resolve()):
        parser.error("output must be under the ignored store")
    old_ui = cdhash(args.ui_original)
    new_ui = cdhash(args.candidate / "app-store-ui")
    stubs = args.stub_list.read_text().splitlines()
    matched = []
    for index, line in enumerate(stubs):
        fields = line.split(None, 3)
        if len(fields) == 4 and fields[3] == UI:
            if fields[0] != old_ui:
                parser.error("unexpected ui identity")
            matched.append(index)
            stubs[index] = "  ".join([new_ui, *fields[1:]])
    if len(matched) != 1:
        parser.error("unexpected ui identities")
    native = args.native_list.read_text().splitlines()
    additions = []
    for name in ("appstoreagent", "appstored"):
        source = originals[name]
        subprocess.run(["codesign", "--verify", "--arch", "x86_64", "-R=anchor apple", str(source)], check=True)
        path = ("usr/lib/m27/store-policy-original-" + name if args.flat_originals else
                "usr/lib/m27/originals/" + SUPPORT + name)
        native = [line for line in native if not
                  (len(fields := line.split(None, 3)) == 4 and fields[3] == path)]
        row = [cdhash(args.candidate / name), cdhash(source), "x86_64", path]
        native.append("  ".join(row))
        additions.append(dict(path=path, observed=row[0], reference=row[1], sha256=sha(source)))
    output.mkdir(parents=True, exist_ok=False)
    (output / "stub-identities").write_text("\n".join(stubs) + "\n")
    (output / "native-identities").write_text("\n".join(native) + "\n")
    rollback = output / "rollback"
    rollback.mkdir()
    for name, source in originals.items():
        shutil.copyfile(source, rollback / name)
        (rollback / name).chmod(0o755)
    shutil.copyfile(args.stub_list, rollback / "stub-identities")
    shutil.copyfile(args.native_list, rollback / "native-identities")
    result = {"candidate_files": record["files"],
              "original_files": record["input_sha256"],
              "original_lists": {"stub-identities": sha(args.stub_list), "native-identities": sha(args.native_list)},
              "updated_lists": {name: sha(output / name) for name in ("stub-identities", "native-identities")},
              "ui_stub": {"before": old_ui, "after": new_ui}, "native_originals": additions}
    (output / "identities.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
