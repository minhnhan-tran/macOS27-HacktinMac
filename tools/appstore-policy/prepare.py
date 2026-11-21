import argparse
import hashlib
import json
import pathlib
import subprocess
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
INPUT_HASHES = json.loads((HERE / "profiles/inputs-26B131.json").read_text())
AGENT_SHA256 = INPUT_HASHES["agent"]
DAEMON_SHA256 = INPUT_HASHES["daemon"]
UI_SHA256 = INPUT_HASHES["ui"]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def slice_hashes(path):
    data = path.read_bytes()
    magic, count = struct.unpack_from(">II", data)
    if magic not in (0xcafebabe, 0xcafebabf) or not 0 < count <= 64:
        raise ValueError("expected a universal Mach-O")
    wide = magic == 0xcafebabf
    result = {}
    for index in range(count):
        at = 8 + index * (32 if wide else 20)
        cpu, subtype = struct.unpack_from(">II", data, at)
        start, length = struct.unpack_from(">QQ" if wide else ">II", data, at + 8)
        key = f"{cpu:08x}:{subtype:08x}"
        if key in result or start + length > len(data):
            raise ValueError("invalid universal Mach-O slice table")
        result[key] = hashlib.sha256(data[start:start + length]).hexdigest()
    return result

def code_directories(data):
    if data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("expected a thin Mach-O")
    at = 32
    for _ in range(struct.unpack_from("<I", data, 16)[0]):
        cmd, size = struct.unpack_from("<II", data, at)
        if size < 8 or at + size > len(data):
            raise ValueError("invalid load command")
        if cmd == 0x1d:
            start, length = struct.unpack_from("<II", data, at + 8)
            magic, total, count = struct.unpack_from(">III", data, start)
            if magic != 0xfade0cc0 or total > length or start + length > len(data):
                raise ValueError("invalid embedded signature")
            result = {}
            for index in range(count):
                slot, offset = struct.unpack_from(">II", data, start + 12 + 8 * index)
                if slot == 0 or 0x1000 <= slot < 0x1006:
                    cd = start + offset
                    if struct.unpack_from(">I", data, cd)[0] != 0xfade0c02:
                        raise ValueError("invalid CodeDirectory")
                    result[data[cd + 37]] = cd
            return result
        at += size
    raise ValueError("missing signature")

def preserve_bundle_slots(original, edited):
    source = original.read_bytes()
    target = bytearray(edited.read_bytes())
    old, new = code_directories(source), code_directories(target)
    for kind, previous in old.items():
        p_hash, p_count = struct.unpack_from(">I", source, previous + 16)[0], struct.unpack_from(">I", source, previous + 24)[0]
        p_size = source[previous + 36]
        for slot in (1, 3):
            value = source[previous + p_hash - slot * p_size:previous + p_hash - (slot - 1) * p_size] if slot <= p_count else b""
            if not any(value):
                continue
            current = new.get(kind)
            if current is None or not (struct.unpack_from(">I", target, current + 12)[0] & 2):
                raise ValueError("cannot retain bundle slot in this signature")
            t_hash, t_count = struct.unpack_from(">I", target, current + 16)[0], struct.unpack_from(">I", target, current + 24)[0]
            if target[current + 36] != p_size or slot > t_count:
                raise ValueError("CodeDirectory has no room for bundle slot")
            begin = current + t_hash - slot * p_size
            target[begin:begin + p_size] = value
    edited.write_bytes(target)

def prepare_executable(source, target, spec, record):
    before = slice_hashes(source)
    with tempfile.TemporaryDirectory(prefix="appstore-sign-") as temp:
        thin = pathlib.Path(temp) / "original"
        edited = pathlib.Path(temp) / "edited"
        subprocess.run(["xcrun", "lipo", str(source), "-thin", "x86_64", "-output", str(thin)], check=True)
        subprocess.run([sys.executable, "-I", str(REPO / "tools/edit-engine/edit-engine"),
                        "apply", "--sign", "--record", str(record),
                        str(spec), str(thin), str(edited)], check=True)
        preserve_bundle_slots(thin, edited)
        subprocess.run(["xcrun", "lipo", str(source), "-replace", "x86_64", str(edited),
                        "-output", str(target)], check=True)
    after = slice_hashes(target)
    if before.keys() != after.keys() or any(before[k] != after[k] for k in before
                                            if not k.startswith("01000007:")):
        raise ValueError("non-Intel slices changed")

    if "app-store-ui" not in spec.name:
        subprocess.run(["codesign", "--verify", "--strict", str(target)], check=True)
    return {"before": before, "after": after}

def compile_library(library, trace=False):
    compiled_sources = [HERE / "src/store_policy.m"]
    if trace:
        compiled_sources.append(HERE / "probes/capability_trace.m")
    subprocess.run(["xcrun", "clang", "-arch", "x86_64", "-fobjc-arc", "-dynamiclib",
                    "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                    "-framework", "Foundation", "-install_name",
                    "/usr/lib/m27/libm27-appstore-policy.dylib",
                    *map(str, compiled_sources), "-o", str(library)], check=True)
    subprocess.run(["codesign", "-s", "-", str(library)], check=True)
    return compiled_sources

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--daemon", type=pathlib.Path)
    parser.add_argument("--ui", type=pathlib.Path)
    parser.add_argument("--trace-capabilities", action="store_true")
    args = parser.parse_args()
    if args.trace_capabilities and not args.ui:
        parser.error("--trace-capabilities requires --ui")
    if sha(args.agent) != AGENT_SHA256:
        parser.error("unexpected agent")
    if args.daemon and sha(args.daemon) != DAEMON_SHA256:
        parser.error("unexpected daemon")
    if args.ui and sha(args.ui) != UI_SHA256:
        parser.error("unexpected ui")
    output = args.output.resolve()
    if not output.is_relative_to((REPO / "store").resolve()):
        parser.error("output outside store")
    output.mkdir(parents=True, exist_ok=False)
    library = output / "libm27-appstore-policy.dylib"
    compiled_sources = compile_library(library, args.trace_capabilities)
    sources = [*compiled_sources, HERE / "profiles/inputs-26B131.json",
               pathlib.Path(__file__).resolve()]
    files = [library]
    inputs = {"appstoreagent": args.agent}
    if args.daemon:
        inputs["appstored"] = args.daemon
    if args.ui:
        inputs["app-store-ui"] = args.ui
    slice_records = {}
    for name, source in inputs.items():
        spec = HERE / f"profiles/{name}-26B131.json"
        target = output / name
        slice_records[name] = prepare_executable(source, target, spec, output / f"{name}-edits.json")
        sources.append(spec)
        files.append(target)
    record = {
        "target_build": "26B131",
        "diagnostic_capability_trace": args.trace_capabilities,
        "input_sha256": {name: sha(path) for name, path in inputs.items()},
        "source_sha256": {str(p.relative_to(HERE)): sha(p) for p in sources},
        "files": {p.name: sha(p) for p in files},
        "slices": slice_records,
    }
    (output / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))

if __name__ == "__main__":
    main()
