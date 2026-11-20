# hao-contract

Checks the files that pass between the macOS27Tool repositories: macos27tool-runtime's `generated.lock` and payload
`payload-manifest.json`, and public hygiene (no home folders or private names in tracked files or commit
messages). Standard library only, Python 3.9 or later. This is the checker's home; other repositories vendor a
pinned copy and declare their checks in their own `contract.json`.

```sh
python3 -I tools/hao-contract/hao_contract.py check REPO      # the checks REPO/contract.json names
python3 -I tools/hao-contract/hao_contract.py message FILE    # hygiene on one commit message
python3 -I tools/hao-contract/hao_contract.py release FILE [DIR] # a release.json, and the asset files staged in DIR
python3 -I tools/hao-contract/hao_contract.py --version
```

Private names are not in this repository. The hygiene check reads them from `$HAO_CONTRACT_PRIVATE` or
`~/.config/hao-contract/private-names`, one `LABEL: TEXT` per line; without that file it checks home folders only.
The labels are the ones a `contract.json` `allow` list names.

## Commit hooks

[`hooks/`](hooks) holds a pre-commit and a commit-msg hook that refuse a commit before it exists. A message, or a line
the staged change adds, is refused if it names a home folder, a private name or a private pattern. It is also refused
if it uses internal wording on the wording list; for the staged change, that applies to documentation only (`*.md`,
`patch.json`). The lists live outside the repository; a local file names them (`KIND PATH` lines: `names`,
`patterns`, `wording`; see `hooks/check.py`). A message must also keep the lengths of the commit-message standard: a subject of at most 72 characters, at most 6
body lines, and one `Tested:` line of at most 160 characters. The output names the label and the file and line, never the matched text.

```sh
sh tools/hao-contract/hooks/install --lists LISTS-FILE REPO...   # once per repository; again after hooks/ changes
```

They are installed into the repository's shared hooks folder, so every worktree runs them whatever its branch. The
pre-push hook already there is left alone. `core.hooksPath` is not used: pointed at a tracked folder, it would
replace that pre-push hook, and a worktree whose branch lacks the folder would run no hook at all.

Schemas for `contract.json`, `generated.lock`, `payload-manifest.json` and `release.json` are in [`schemas/`](schemas); the tests
keep them in step with the checker.

```sh
python3.14 -I -B -m unittest discover -s tools/hao-contract/tests
```
