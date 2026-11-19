# edit-engine

Applies declared file edits. A recipe that changes a file someone else made (Apple, a Tahoe donor, OCLP) writes the
change down as an **edit spec**, `patches/NAME/edits/FILE.json`: where the change goes, the bytes it expects, the
bytes it writes, and why. This tool applies a spec to a file, checks every write before and after, and explains
specs in plain text. `pipeline/patches.py check` validates every spec, and `patches.py explain` and
[docs/PATCHES.md](../../docs/PATCHES.md) render them.

Each file is described as one origin, then ordered edits, then one signature:

```
final file = sign( edit_n( … edit_1( origin ) ) )
```

## Usage

Python 3.9 or later, standard library only. From the repository root:

```sh
python3.14 -I tools/edit-engine/edit-engine apply SPEC IN OUT                 # one line per edit
python3.14 -I tools/edit-engine/edit-engine apply --show-edits SPEC IN OUT    # + bytes, instructions, reasons
python3.14 -I tools/edit-engine/edit-engine apply --quiet SPEC IN OUT         # failures only
python3.14 -I tools/edit-engine/edit-engine apply --log json SPEC IN OUT      # one JSON event per line
python3.14 -I tools/edit-engine/edit-engine apply --sign --record R.json SPEC IN OUT
python3.14 -I tools/edit-engine/edit-engine apply --sign --entitlements-from FILE SPEC IN OUT   # sign.entitlements_from
python3.14 -I tools/edit-engine/edit-engine apply --store STORE SPEC IN OUT   # keep, and reuse when hashes match
python3.14 -I tools/edit-engine/edit-engine apply --target pipeline/targets/T.json SPEC IN OUT   # for when_target
python3.14 -I tools/edit-engine/edit-engine inspect STORE PATH                # original and patched side by side
python3.14 -I tools/edit-engine/edit-engine check SPEC ...
python3.14 -I tools/edit-engine/edit-engine explain SPEC ...
```

`apply` never changes IN. It writes OUT only when every edit applied and every check passed. Otherwise it exits 1
and the message names the edit. `--sign` re-signs OUT ad hoc as the spec's `sign` says (macOS `codesign`); a spec whose `sign` takes another
file's entitlements needs that file as `--entitlements-from`.
`--record` writes the input and output SHA-256, each edit's sites (offset, bytes before and after) and whether the
output matches the spec's recorded one.

## Targets (when_target)

A spec with `when_target` needs the target to be known; without one it is refused (exit 2), never guessed. Every
applier gets it:

- `edit-engine apply`: `--target TARGET.json`, or `--target-data FILE` (the `platform_data` alone), by default the
  file in `HAO_TARGET_DATA`, which every hook gets. On another target OUT is a copy of IN, the output says
  `skipped`, `--log json` prints one `file.skipped` event and `--record` records the reason; exit 0.
- `stub-edit`: `--target-data FILE`, by default `HAO_TARGET_DATA`; skipped means nothing is written.
- `pipeline/materialize.py`: the target it is given (golden-run's assembly target). A skipped spec is left out of its
  path's writers (`skipped_writers` in the report); a path with no writer left is `skipped`, and the overlay plan
  keeps its recipe on the hook, which sees the same target.
- `macOS27Tool patch` and the installer apply materialised overlays, made for one target, and read no specs.

## Artifact store

With `--store STORE`, every apply keeps its original, its result and its record, and is inspectable afterwards:

```
STORE/objects/<sha256>                    content-addressed bytes (originals and results)
STORE/results/<key>.json                  one apply: input/output sha256 and the full record
STORE/view/<path>/original, patched       links to the two objects, side by side
STORE/view/<path>/record.json, explain.txt
```

`key` is the sha256 of the engine version, the canonical spec, the input's sha256 and the source of every matcher
function the spec names. When a stored result has the same key and its output object still hashes to the
recorded sha256, the build reuses it and does not re-run the edits (`reused` in the output, `file.reused` in the
JSON log). A damaged object, or a changed spec, input, matcher or engine version, means the edits run again.
`inspect` prints where both files are, their hashes, the changed byte ranges and the explanation.

## Spec format 1

```json
{
 "format": 1,
 "path": "System/Library/Frameworks/Metal.framework/Versions/A/Metal",
 "arch": "x86_64",
 "origin": {"kind": "oclp", "name": "Metal 3802 Common"},
 "applied_by": "installer",
 "edits": [
  {"id": "hello-not-skipped", "kind": "literal",
   "base": "_fake_LCAUtilities_shouldSkipHello", "limit": 64,
   "find": "B0 01 0F BE C0 48 83 C4 10 5D C3", "replace": "B0 00 0F BE C0 48 83 C4 10 5D C3",
   "asm": {"before": "mov al,1", "after": "mov al,0"},
   "reason": "Language Chooser plays the hello animation"}
 ],
 "sign": {"mode": "adhoc", "identifier": "Metal"},
 "expect": {"INPUT_SHA256": "OUTPUT_SHA256"}
}
```

| Key | Meaning |
|---|---|
| `path` | the file on the installed system, relative to its root |
| `arch` | `x86_64`: the file is a Mach-O (thin, or universal with one x86_64 slice) and must still parse after the edits |
| `origin` | who made the bytes this spec edits: `kind` `apple` (Golden Gate), `tahoe`, `oclp` or `hao`, with optional `name`, `row` (input row) and `note` |
| `applied_by` | `installer` (default), `opencore` or `kext:IntelCommpage` |
| `when_target` | optional: a key of the target's `platform_data` ([docs/layout.md](../../docs/layout.md#target-platform-data)). The spec applies only on a target that has it and is skipped on any other (reported as skipped, not an error). Without it the spec applies on every target |
| `edits` | applied in order; each sees the result of the ones before |
| `sign` | `null`, or `{"mode": "adhoc"}` with `"identifier": NAME` or `"preserve": [identifier, entitlements, requirements, flags, runtime]` (keep those from the input file, as `codesign -i <its identifier> --preserve-metadata=…`), and optionally `"entitlements_from"`: the entitlements of another signed file, `{"kind": apple\|tahoe\|oclp\|ventura, "path": P}` (that layer's file at P) or `{"kind": "hao", "row": ROW, "source": P}`, read with `codesign -d --entitlements - --xml` and signed with `--entitlements`, as a hook that gives a program another program's identity does (not with `preserve` entitlements): signed once, after the last edit |
| `expect` | input SHA-256 → output SHA-256 recorded for that input. A different output is reported, not refused |

Every edit has `id` (lower-case letters, digits, `-`), `kind`, `reason`, and optionally `evidence` and `asm`
(`before`/`after` text).
A `plist.set` of `boot-args` also has `arg_reasons`: each argument of the value, exactly as written, mapped to why
it is there. An argument without a reason, or a reason for an argument that is not there, is refused.

**literal**: OpenCore-style find and replace. `find` and `replace` are hex bytes of the same length. `mask` selects
which bits of `find` must match, and `replace_mask` selects which bits `replace` writes. `count` (default 1) is how
many matches there must be, no more and no fewer. Give exactly one anchor:

- `base` + `limit`: search the `limit` bytes from a defined symbol (preferred: survives relinks)
- `section`: search one section, `"__TEXT,__text"`
- `at`: one file offset (`"0x…"`)

If `find` is not there but `replace` is, the error says the file was edited before.

**matcher**: sites that code has to compute. `resolved` maps an input SHA-256 to its checked-in sites
(`at`, `before`, `after`), so the exact bytes can be read without running anything. For an input with no resolved
sites, `fn` (`"relative/path.py:function"`, called with the file's bytes, returning `(offset, before, after, …)`
tuples) computes them. Every site's `before` bytes must be there.

**op**: structural changes.

| op | args |
|---|---|
| `macho.add-dylib` | `path` (absolute), `weak`, `timestamp` (default 2, as ld64 writes), `current_version`, `compat_version`. Uses the zero padding after the load commands and leaves the signature for `sign` |
| `plist.set` | `key` (list of dictionary keys and array indexes), `value`. Keeps XML or binary format. Not mixed with byte edits in one spec |
| `metallib.rewrite`, `kc.link`, `cryptex.build`, `hao.embed-stub`, `hao.stamp`, `kc.bind` | declared for `explain`; applied by their own tools, so `apply` refuses them (a spec may still list one next to `plist.set` edits). `pipeline/materialize.py` defers a file with one to the target. `hao.stamp` (the hao-runtime row's `stamp_identity.py`, HaoExec's BootKC identity) and `kc.bind` (`tools/kc-binder`: OpenCore's kernel repairs computed from the BootKC) name the installed file they read as `kernel_collection`, and the deferred step comes after it |

Checks: every literal and matcher site is checked before it is written. Two edits that touch the same bytes (or
the same plist key) are refused. After the last edit, every written byte must still be what its edit wrote, and an
`arch` file must still parse.

## Signatures

`src/signing.py` signs as a spec says (one codesign run, on a temporary copy), reads a signature back (each slice's
identifier, CodeDirectory flags, special-slot count, CDHash, requirements, entitlements and DER entitlements) and
`compare()`s two files: the bytes outside the signature (with `LC_CODE_SIGNATURE` and `__LINKEDIT` sizes masked),
then the identifier, flags, entitlements and requirements. That is what equal means for a signed file:

- **Signature padding is not a difference.** The builder VM's codesign reserves about 18 KB of zeros after the
  blobs in each slice; the host's reserves none. The CDHash, the file size and the signature's size fields differ,
  nothing else: `compare()` lists it as a note.
- **The metadata is.** A file with the right code but a signature without the entitlements its hook gave it (the
  27.x WindowServer's, for Tahoe's WindowServer) is refused, however equal its code is.
- **Anything keyed on CDHashes is made from the installed files.** `usr/lib/hao/native-identities`
  (golden-gate-native-identities) lists the observed CDHash of each re-signed native program; an overlay signed on
  another machine has other CDHashes, if only from the padding. The recipe declares its files
  `"after": ["overlays"]`, and `pipeline/materialize.py` defers them and orders them after every overlay file and
  every other deferred step (`DIR/deferred.json`).

## Editing a stub hao generated

hao-runtime's installer gives each ARM64-only program an x86_64 stub slice and lists it in
`usr/lib/hao/stub-identities` (stub CDHash, Apple's arm64 CDHash, arch, path). A recipe that must change such a stub
(add a library to it, say) cannot ship a copy of the whole stub: the stub belongs to the payload of the image being
built. `stub-edit` makes the change in that image, from the recipe's kc-hook, in a set position after hao-runtime:

```sh
"$OCLP_PYTHON" -I "$H/inputs/edit_engine/stub-edit" "$1" SPEC [--record FILE]   # recipe input row: edit_engine
```

SPEC is an ordinary spec with `arch` x86_64 and `origin.kind` hao. stub-edit refuses before writing anything unless
the program's x86_64 CDHash is its stub-identities stub CDHash and its arm64 slice has the listed Apple CDHash (the
stub this image's installer embedded). It then edits and signs the x86_64 slice alone, carries the stub's bundle
binding (special slots -1 Info.plist and -3 resources, which codesign cannot compute for a lone slice) into the new
signature, puts the slice back with every other slice byte for byte (checked), writes the file in place, and rewrites
that one stub-identities line with the new stub CDHash. `usr/lib/hao/trusted-cdhashes` is keyed on Apple's arm64
slices and does not change. Checked on a 26B131 application stub: every slice and the new CDHash equal the same
edit made by hand with lipo (only the slice order differs: stub-edit keeps the installer's). Tests:
`tests/test_stub_edit.py`.

The same tool edits an Apple universal program (`origin.kind` apple, e.g. a daemon whose x86_64 code a recipe
changes): its lineage is its sha256, which must be a key of the spec's `expect`; the x86_64 slice alone is edited and
signed as above, every other slice kept byte for byte, and no stub-identities line is touched
(golden-gate-native-identities lists the re-signed program). An output other than `expect`'s is reported, not refused:
signature padding differs between Macs. Checked on a 26B131 daemon: every slice and CDHash equal the hand-made edit;
only the slice offsets differ (stub-edit keeps the input's alignment, lipo used 4 KB).

`src/signlint.py` is the static half: a recipe whose hook signs with another file's entitlements
(`codesign … --entitlements FILE`) or keeps the input's metadata (`--preserve-metadata=…`) needs a signed spec that
says so (`entitlements_from`, `preserve`). `verify-declaration` runs both: signlint on every changed recipe, and
`compare()` against `--reference PATH=FILE` files the hooks installed (e.g. read from a built image).

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/edit-engine/tests
python3.14 -I tools/edit-engine/src/signlint.py patches/*/
python3.14 -I -B -m unittest discover -s patches/golden-gate-hello/tests   # HELLO_OCLP_METAL=PATH: OCLP's wrapper too
```

The tests build synthetic x86_64 Mach-O files (thin and universal), so they need no Apple binaries. They cover
symbol, section and offset anchors, masks, counts, already-applied files, overlaps, matchers, dylib insertion and its
refusals, plist edits, refused external ops, the `expect` note, the result-parses check, each command-line detail
level, `when_target` (validated, applied with the key, skipped without it, refused with no target), plus the store: reuse, the side-by-side view, re-applying after damage or any change to the key's parts,
and `inspect`. `golden-gate-hello`'s test proves its spec writes the same bytes as its `edit-metal.py`.

## Status

The first piece of the declared-edit catalog (workflow plan step 4). Recipe hooks still run their own scripts, and
specs are proven equal to them by tests. Hooks move onto the engine one recipe at a time.
