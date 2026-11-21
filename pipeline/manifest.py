#!/usr/bin/env python3.14
"""manifest: what is installable, what each built image has, and what is on the test laptops (docs/MANIFEST.md).

  patches.py manifest [OUT]                    write docs/MANIFEST.md (default) from the tracked records below
  patches.py manifest --record RUN [--label L] add or replace the image built by golden-run's record folder RUN
                                               (store runs/<job>) in pipeline/images.json
  patches.py manifest --laptop NAME IMAGE [--pushed "YYYY-MM-DD HH:MM:SS"] [--ledger FILE]
                                               record that IMAGE (an images.json id) was pushed to laptop NAME, and take
                                               the swap ledger's swaps on NAME since then into pipeline/laptops.json
  patches.py manifest --laptops [--ledger FILE] refresh every laptop's swaps from the ledger (same push)
  --verify (with --laptop/--laptops)           also read each swapped file on the laptop over SSH (read only): there,
                                               replaced (other bytes, e.g. restored) or gone
  patches.py manifest --private                print the local view: each row's store path and whether it resolves here

The document is generated from tracked files only, so the pipeline tests can tell when it is stale: patches/*/patch.json
(revision, status, stage, inputs, "files"), the input rows of the dev assembly's builds (pipeline/assemblies/
dev-26B131.json: inputs/golden/BUILD, inputs/DONOR, inputs/common), pipeline/sets/*.txt, pipeline/reproductions.json,
pipeline/live-swaps.tsv, and two records this tool writes from the store:
  pipeline/images.json   {ID: {label, repo, built, commit, dirty, set, target, golden_build, donor_build, revisions
                         {patch: N}, rows {row: sha256}, optional {ran, skipped}}}: ID is the image repository commit
                         (what bench pushes), the rest comes from the build record (build.json, MANIFEST.txt, inputs.json)
  pipeline/laptops.json  {NAME: {label, image, pushed, cleared, swaps [{date, path, sha256, proven, payload, now}]}}: the
                         newest swap of each laptop path since the push (the ledger's laptop column), proven = the
                         ledger has a proven line for it, payload = the m27_runtime row's payload holds the same bytes
                         (swaps.py's reading), now = what --verify read on the laptop (the ledger does not record
                         restores); swaps --verify found gone or replaced are only counted (cleared). NAME is the
                         ledger's laptop name; label is what the document calls it (set by hand)
Store paths, hostnames, who made a swap and scratch folder names (public_path) stay out of both records and the
document; --private shows the store paths.

Installable: a patch whose input rows all have a row file with a sha256 in those locks and that declares its files.
"""
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time

TOP = Path(__file__).resolve().parents[1]
IMAGES = TOP / 'pipeline' / 'images.json'
LAPTOPS = TOP / 'pipeline' / 'laptops.json'
OUT = TOP / 'docs' / 'MANIFEST.md'
ASSEMBLY = TOP / 'pipeline' / 'assemblies' / 'dev-26B131.json'
SETS = TOP / 'pipeline' / 'sets'
GROUP = 10          # more whole files than this from one origin and folder are one line


def _patches():
    return runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))


def _lock():
    return runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))


def _swaps():
    return runpy.run_path(str(TOP / 'pipeline' / 'swaps.py'))


def load(path):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else {}


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=1, sort_keys=True) + '\n')


def locks(assembly=ASSEMBLY):
    a = json.loads(Path(assembly).read_text())
    base = TOP / 'pipeline' / 'inputs'
    return [base / 'golden' / a['golden_build'], base / a['donor_build'], base / 'common']


def row_files(lock_dirs):
    """{row: (sha256 or '-', lock folder name, artifact name, in the checkout)} for the first lock folder that has each
    row (a '-' row of a tracked checkout file is complete: the commit pins it)."""
    L = _lock()
    out = {}
    for d in lock_dirs:
        if not d.is_dir():
            continue
        for name, (sha, p) in L['rows'](str(d)).items():
            out.setdefault(name, (sha, os.path.relpath(d, TOP / 'pipeline' / 'inputs'), os.path.basename(p.rstrip('/')),
                                  L['in_checkout'](p)))
    return out


def patch_locks(p, lock_dirs):
    """The lock folders a patch's rows come from: the assembly's, with the builds its "applies" names before common."""
    builds = p.get('applies', {}).get('builds', []) if isinstance(p.get('applies'), dict) else []
    extra = [TOP / 'pipeline' / 'inputs' / b for b in builds]
    return [d for d in lock_dirs[:-1]] + [d for d in extra if d not in lock_dirs] + lock_dirs[-1:]


def short_resolution(r):
    """'row NAME', 'recipe NAME' or 'dropped' (the reasons are in pipeline/live-swaps.tsv)."""
    return 'dropped' if r.startswith('dropped') else r.split(':', 1)[0]


def sets_of(root=TOP / 'patches'):
    """{patch: [set names that list it]}."""
    P = _patches()
    out = {}
    for s in sorted(SETS.glob('*.txt')):
        for n in P['names'](str(s)):
            out.setdefault(n, []).append(s.stem)
    return out


def patch_info(root=TOP / 'patches'):
    out = {}
    for n in sorted(os.listdir(root)):
        pj = root / n / 'patch.json'
        if pj.is_file():
            out[n] = json.loads(pj.read_text())
    return out


def short(sha):
    return sha[:8] if sha and sha != '-' else '-'


def md_cell(text):
    return str(text).replace('|', '/').replace('\n', ' ')


# ---- the records -------------------------------------------------------------------------------------------------

def stamp_time(stamp):
    """Epoch seconds of a 'YYYYMMDD-HHMMSS' stamp (local time)."""
    return time.mktime(time.strptime(stamp, '%Y%m%d-%H%M%S'))


def image_commit(repository, commit, after):
    """The image repository commit golden-run made for build commit COMMIT: its subject ends with the commit and it is
    the first one at or after AFTER (epoch seconds), or None."""
    log = subprocess.run(['git', '-C', str(repository), 'log', '--format=%h %ct %s', '--abbrev=7'],
                         capture_output=True, text=True, check=True).stdout.splitlines()
    hits = []
    for line in log:
        h, ct, subject = line.split(' ', 2)
        if subject.endswith(commit) and int(ct) >= after - 60:
            hits.append((int(ct), h))
    return min(hits)[1] if hits else None


def revisions_of(manifest_text):
    """{patch: N} from MANIFEST.txt's 'set NAME: patch rN ...' line."""
    for line in manifest_text.splitlines():
        if line.startswith('set ') and ':' in line:
            words = line.split(':', 1)[1].split()
            return {words[i]: int(words[i + 1][1:]) for i in range(0, len(words) - 1, 2) if words[i + 1].startswith('r')}
    raise ValueError('MANIFEST.txt has no "set NAME: ..." line')


def record_image(run, label=None, images=IMAGES):
    """Add the image of golden-run's record folder RUN to images.json; returns its id."""
    run = Path(run)
    b = json.loads((run / 'build.json').read_text())
    if b.get('status') != 'committed':
        raise ValueError('%s: status %s, not a committed image' % (run, b.get('status')))
    stamp = re.search(r'(\d{8}-\d{6})', run.name).group(1)
    ident = image_commit(b['repository'], b['commit'], stamp_time(stamp))
    if not ident:
        raise ValueError('%s: no commit in %s names build commit %s' % (run, b['repository'], b['commit']))
    r = b['recipe']
    inputs = json.loads((run / 'inputs.json').read_text())
    opt = b.get('optional') or {}
    data = load(images)
    old = data.get(ident, {})
    data[ident] = {
        'label': label if label is not None else old.get('label', ''),
        'repo': os.path.basename(r['repo'].rstrip('/')),
        'built': time.strftime('%Y-%m-%d %H:%M', time.localtime(stamp_time(stamp))),
        'commit': b['commit'][:8], 'dirty': bool(b.get('dirty')),
        'set': r['patch_set'], 'target': r['target'], 'golden_build': r['golden_build'], 'donor_build': r['donor_build'],
        'revisions': revisions_of((run / 'MANIFEST.txt').read_text()),
        'rows': {k: v['sha256'] for k, v in sorted(inputs.items())},
        'optional': {'ran': opt.get('ran', {}), 'skipped': opt.get('skipped', {})},
    }
    save(images, data)
    return ident


SCRATCH = ('/private/tmp/', '/private/var/tmp/', '/tmp/', '/var/tmp/')


def public_path(path):
    """PATH, or for a file in a scratch folder (whose name says who made it) the scratch root and the file name."""
    for root in SCRATCH:
        if path.startswith(root) and '/' in path[len(root):]:
            return root + '…/' + path.rsplit('/', 1)[1]
    return path


def laptop_files(name, paths):
    """{path: sha256, "folder" or None (not there)} read over SSH on laptop NAME (read only; M27_LAPTOP_SSH names the ssh tool,
    LAPTOP_NAME selects the laptop), or None when the laptop does not answer."""
    import shlex
    script = '; '.join('printf "%%s\\t" %s; if [ -d %s ]; then echo folder; else (shasum -a 256 -- %s 2>/dev/null || echo -) | cut -d" " -f1; fi'
                       % (shlex.quote(str(i)), shlex.quote(p), shlex.quote(p)) for i, p in enumerate(paths))
    tool = os.environ.get('M27_LAPTOP_SSH')
    if not tool:
        sys.exit('manifest: --verify needs M27_LAPTOP_SSH, the ssh tool that takes LAPTOP_NAME and -c SECONDS')
    try:
        r = subprocess.run([tool, '-c', '15', script], capture_output=True,
                           text=True, timeout=120, env=dict(os.environ, LAPTOP_NAME=name))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    out = {}
    for line in r.stdout.splitlines():
        i, _, sha = line.partition('\t')
        if i.isdigit() and int(i) < len(paths):
            sha = sha.strip()
            out[paths[int(i)]] = sha if len(sha) == 64 or sha == 'folder' else None
    return out if len(out) == len(paths) else None


def laptop_swaps(name, pushed, ledger_path=None, verify=False):
    """[{date, path, sha256, proven, payload[, now]}]: the newest ledger swap of each path on laptop NAME at or after
    PUSHED. With VERIFY, now = "there" (the laptop's file is these bytes), "replaced" (other bytes, e.g. restored) or
    "gone", read over SSH; left out when the laptop does not answer."""
    S = _swaps()
    ledger_path = ledger_path or S['LEDGER']
    entries = [e for e in S['ledger'](ledger_path) if e['laptop'] == name and e['date'] >= pushed]
    proofs = {(p['sha256'], p['path']) for p in S['ledger'](ledger_path, proofs=True)}
    payload = S['runtime_payload']()
    newest = {}
    for e in entries:
        newest[e['path']] = e
    now = laptop_files(name, sorted(newest)) if verify and newest else None
    out = []
    for p, e in sorted(newest.items(), key=lambda x: (x[1]['date'], x[0])):
        s = {'date': e['date'], 'path': public_path(p), 'sha256': e['sha256'], 'proven': (e['sha256'], p) in proofs,
             'payload': bool(S['resolution'](e, {}, payload))}
        if now is not None:
            s['now'] = ('there' if now[p] == e['sha256'] else 'gone' if now[p] is None
                        else 'folder there, not compared' if now[p] == 'folder' else 'replaced')
        out.append(s)
    return out


def split(swaps):
    """{swaps: those still on the laptop (or not checked), cleared: how many --verify found gone or replaced}: the
    record keeps only what is there."""
    on = [s for s in swaps if s.get('now') not in ('gone', 'replaced')]
    S = _swaps()
    resolved, public = S['resolutions'](), S['resolutions'](private=False)
    shown = [s for s in on if listed(s, resolved)]
    hidden = sum(1 for s in on if s not in shown and resolution_text(s, resolved) and not resolution_text(s, public))
    return {'swaps': shown, 'cleared': len(swaps) - len(on), 'trials': len(on) - len(shown), 'private': hidden}


def listed(swap, resolved):
    r = resolution_text(swap, resolved) or ''
    return swap['proven'] or r.startswith(('row ', 'recipe '))


def record_laptop(name, image, pushed=None, ledger_path=None, laptops=LAPTOPS, images=IMAGES, verify=False):
    if image not in load(images):
        raise ValueError('%s is not in %s: record its build first (--record RUN)' % (image, images.name))
    data = load(laptops)
    pushed = pushed or time.strftime('%Y-%m-%d %H:%M:%S')
    label = data.get(name, {}).get('label', name)
    data[name] = dict({'image': image, 'pushed': pushed, 'label': label}, **split(laptop_swaps(name, pushed, ledger_path, verify)))
    save(laptops, data)


def refresh_laptops(ledger_path=None, laptops=LAPTOPS, verify=False):
    data = load(laptops)
    for name, v in data.items():
        v.pop('cleared', None)
        v.update(split(laptop_swaps(name, v['pushed'], ledger_path, verify)))
    save(laptops, data)


# ---- the document ------------------------------------------------------------------------------------------------

def files_lines(name, declared, S):
    """Markdown lines for patch NAME's declared files, whole files from one origin and folder grouped past GROUP."""
    mine = [r for r in declared if r[0] == name]
    groups = {}
    for _, _, _, path, origin, spec, entry in mine:
        if spec is None:
            groups.setdefault(json.dumps(origin, sort_keys=True), []).append(path)
    out = []
    shown = set()
    for k, paths in groups.items():
        if len(paths) > GROUP:
            origin = json.loads(k)
            prefix = os.path.commonpath(paths)
            out.append('  - %d files under `%s/`, from %s' % (len(paths), prefix, S['origin_text'](origin)))
            shown.update(paths)
    for _, _, _, path, origin, spec, entry in mine:
        if path in shown:
            continue
        if spec is not None:
            out.append('  - `%s`: edited (%d edit%s) from %s' % (path, len(spec['edits']), '' if len(spec['edits']) == 1
                                                                 else 's', S['origin_text'](spec['origin'])))
        else:
            out.append('  - `%s`%s: from %s' % (path, '/ (folder)' if (origin or {}).get('tree') else '',
                                              S['origin_text'](origin)))
    return out


def resolution_text(swap, resolved):
    r = resolved.get((swap['sha256'], swap['path']))
    if r is None and '\u2026/' in swap['path']:      # a scratch path: the same bytes and file name in a scratch folder
        r = next((v for (sha, p), v in sorted(resolved.items()) if sha == swap['sha256']
                  and public_path(p) == swap['path']), None)
    if r is None and swap.get('payload'):
        r = 'row m27_runtime (same bytes in its payload)'
    return r


def generate():
    P = _patches()
    S = P['edit_spec']()
    sw = _swaps()
    info = patch_info()
    declared = P['declared_files']()
    declaring = {r[0] for r in declared}
    lock_dirs = locks()
    rows = row_files(lock_dirs)
    prow = {n: row_files(patch_locks(p, lock_dirs)) for n, p in info.items()}
    in_sets = sets_of()
    repro = load(TOP / 'pipeline' / 'reproductions.json').get('rows', {})
    images = load(IMAGES)
    laptops = load(LAPTOPS)
    resolved = sw['resolutions']()
    ids = sorted(images, key=lambda i: images[i]['built'])

    def installable(n):
        p = info[n]
        have = prow[n]
        missing = [r for r in p.get('inputs', []) if r not in have or (have[r][0] == '-' and not have[r][3])]
        if missing:
            return 'no: row %s' % ', '.join(missing)
        return 'yes' if n in declaring else 'undeclared'

    md = ['# Manifest: installable and installed', '',
          'Generated by `python3.14 -I pipeline/patches.py manifest` from the patches, their input rows, the sets and the',
          'records pipeline/images.json (built images) and pipeline/laptops.json (the test laptops); the pipeline tests fail',
          'when it is stale. `patches.py manifest --record RUN` adds a build, `--laptop NAME IMAGE` a push,',
          '`--laptops` takes the swap ledger again. Rows are those of %s.' % ', '.join(
              'inputs/' + os.path.relpath(d, TOP / 'pipeline' / 'inputs') for d in lock_dirs), '']
    # 1. installable
    counts = {}
    for n in info:
        counts.setdefault(installable(n).split(':')[0], []).append(n)
    md += ['## 1. Installable', '',
           '%d patches: %d installable, %d undeclared (files not declared), %d with a row missing.' % (
               len(info), len(counts.get('yes', [])), len(counts.get('undeclared', [])), len(counts.get('no', []))), '']
    if counts.get('undeclared'):
        md += ['Undeclared: %s.' % ', '.join(counts['undeclared']), '']
    md += ['| Patch | Rev | Status | Stage | Installable | Sets | Rows (sha256, artifact, source commit) |',
           '|---|---|---|---|---|---|---|']
    for n, p in info.items():
        row_text = []
        for r in p.get('inputs', []):
            sha, where, art, _ = prow[n].get(r, ('?', '?', '?', False))
            src = repro.get(r)
            row_text.append('%s %s %s%s' % (r, short(sha), art, ' (%s %s)' % (src['repo'], src['commit']) if src else ''))
        md.append('| %s | r%s | %s | %s | %s | %s | %s |' % (
            n, p.get('revision'), p.get('status'), p.get('stage'), installable(n), ', '.join(in_sets.get(n, [])) or '-',
            md_cell('; '.join(row_text)) or '-'))
    md += ['', '### Files each patch installs', '']
    for n in info:
        if n in declaring:
            md += ['- **%s** r%s' % (n, info[n].get('revision'))] + files_lines(n, declared, S)
    md.append('')
    # 2. images
    md += ['## 2. Installed in images', '',
           'From each image\'s build record: the commit it was built from, its set, and the patch revisions and row',
           'hashes it has. A cell marked * differs from the current one (section 1).', '']
    if not ids:
        md += ['No images recorded.', '']
    else:
        head = ['%s%s' % (i, ' (%s)' % images[i]['label'] if images[i].get('label') else '') for i in ids]
        md += ['| | ' + ' | '.join(head) + ' |', '|---|' + '---|' * len(ids)]
        for key in ('repo', 'built', 'commit', 'set'):
            md.append('| %s | %s |' % (key, ' | '.join(md_cell(images[i][key]) + (' (dirty)' if key == 'commit' and
                                                                                 images[i]['dirty'] else '') for i in ids)))
        md.append('| optional groups | %s |' % ' | '.join(
            md_cell(', '.join(sorted(set(images[i]['optional']['ran'].values()))) or '-') +
            (' (skipped %s)' % ', '.join(sorted(images[i]['optional']['skipped'])) if images[i]['optional']['skipped'] else '')
            for i in ids))
        names_ = sorted({n for i in ids for n in images[i]['revisions']})
        md += ['', '| Patch | ' + ' | '.join(ids) + ' |', '|---|' + '---|' * len(ids)]
        for n in names_:
            cur = info.get(n, {}).get('revision')
            cells = []
            for i in ids:
                r = images[i]['revisions'].get(n)
                cells.append('-' if r is None else 'r%d%s' % (r, '*' if r != cur else ''))
            md.append('| %s | %s |' % (n, ' | '.join(cells)))
        rnames = sorted({r for i in ids for r in images[i]['rows']})
        md += ['', '| Row | ' + ' | '.join(ids) + ' |', '|---|' + '---|' * len(ids)]
        for r in rnames:
            cur = rows.get(r, ('-',))[0]
            cells = []
            for i in ids:
                s = images[i]['rows'].get(r)
                cells.append('-' if s is None else short(s) + ('*' if s != cur else ''))
            md.append('| %s | %s |' % (r, ' | '.join(cells)))
        md.append('')
    # 3. laptops
    md += ['## 3. On the laptops now', '',
           'The image pushed last, and the newest swap of each path made since that push that is still there (swap',
           'ledger; *On the laptop* is what `--verify` read over SSH, *not checked* without it). Resolution as',
           '`swaps.py` reads it: *proven* = shown to work; *in a recipe/row* or *dropped* from pipeline/live-swaps.tsv;',
           '*not yet in a recipe* = no resolution.', '']
    if not laptops:
        md += ['No laptops recorded.', '']
    for name in sorted(laptops):
        v = laptops[name]
        img = images.get(v['image'], {})
        md += ['### %s' % v.get('label', name), '', 'Image %s%s, pushed %s (built %s from %s, set %s).' % (
            v['image'], ' (%s)' % img['label'] if img.get('label') else '', v['pushed'], img.get('built', '?'),
            img.get('commit', '?'), img.get('set', '?')), '']
        on, off = [s for s in v['swaps'] if listed(s, resolved)], v.get('cleared', 0)
        trials = v.get('trials', 0) + len(v['swaps']) - len(on)
        if trials:
            md += ['%d trial swap%s not listed%s.' % (trials, '' if trials == 1 else 's', ' (%d resolved in the private '
                   'resolution file)' % v['private'] if v.get('private') else ''), '']
        if not on:
            md += ['No swapped files on it%s.' % (' (%d since the push are no longer there)' % off if off else ''), '']
            continue
        if off:
            md += ['%d more swap%s since the push %s no longer there (restored or removed; read over SSH).' % (
                off, '' if off == 1 else 's', 'is' if off == 1 else 'are'), '']
        md += ['| Swapped | Path | sha256 | State | On the laptop |', '|---|---|---|---|---|']
        for s in on:
            r = resolution_text(s, resolved)
            state = ('proven, ' if s['proven'] else 'trial, ') + (short_resolution(r) if r else 'not yet in a recipe')
            md.append('| %s | `%s` | %s | %s | %s |' % (s['date'], md_cell(s['path']), short(s['sha256']), md_cell(state),
                                                    s.get('now', 'not checked')))
        md.append('')
    # 4. drift
    md += ['## 4. Drift', '']
    drift = []
    for n, p in info.items():
        built = [images[i]['revisions'][n] for i in ids if n in images[i]['revisions']]
        if built and p.get('revision') > max(built):
            drift.append('- %s r%s is newer than every image built with it (newest r%d).' % (n, p.get('revision'), max(built)))
    for r, (sha, _, _, _) in sorted(rows.items()):
        had = [images[i]['rows'][r] for i in ids if r in images[i]['rows']]
        if had and sha != '-' and sha not in had:
            drift.append('- row %s %s is in no image yet (images have %s).' % (r, short(sha), ', '.join(sorted({short(h) for h in had}))))
    for name in sorted(laptops):
        for s in laptops[name]['swaps']:
            if not listed(s, resolved):
                continue
            r = resolution_text(s, resolved)
            if s.get('now', 'there') in ('there', 'folder there, not compared') and not (r or '').startswith('dropped'):
                drift.append('- %s: `%s` (%s) differs from image %s: %s.' % (
                    laptops[name].get('label', name), s['path'], short(s['sha256']), laptops[name]['image'],
                    short_resolution(r) if r else 'not yet in a recipe'))
    md += (drift or ['None.']) + ['']
    return '\n'.join(md)


def private_view():
    L = _lock()
    lines = []
    for d in locks():
        if not d.is_dir():
            continue
        for name, (sha, p) in sorted(L['rows'](str(d)).items()):
            try:
                ok = 'resolves' if os.path.exists(p) and (sha == '-' or L['row_hash'](str(d), name, p) == sha) else 'MISSING'
            except SystemExit as e:
                ok = 'ERROR %s' % e
            lines.append('%-40s %-10s %s %s' % (name, ok, short(sha), p))
    S = _swaps()
    resolved = S['resolutions']()
    for name, v in sorted(load(LAPTOPS).items()):
        lines += ['', '%s (%s): image %s, pushed %s' % (name, v.get('label', name), v['image'], v['pushed'])]
        for e in laptop_swaps(name, v['pushed']):
            r = resolution_text(e, resolved)
            lines.append('%s  %s  %s  %s' % (e['date'], short(e['sha256']), ('proven, ' if e['proven'] else 'trial, ') +
                                             (r or 'not yet in a recipe'), e['path']))
    return '\n'.join(lines)


def main(argv):
    args = list(argv)

    def opt(flag, default=None):
        if flag in args:
            i = args.index(flag)
            value = args[i + 1]
            del args[i:i + 2]
            return value
        return default
    ledger_path = opt('--ledger')
    verify = '--verify' in args
    args = [a for a in args if a != '--verify']
    label = opt('--label')
    pushed = opt('--pushed')
    if args[:1] == ['--record'] and len(args) == 2:
        print(record_image(args[1], label))
    elif args[:1] == ['--laptop'] and len(args) == 3:
        record_laptop(args[1], args[2], pushed, ledger_path, verify=verify)
    elif args == ['--laptops']:
        refresh_laptops(ledger_path, verify=verify)
    elif args == ['--private']:
        print(private_view())
        return 0
    elif len(args) <= 1 and not (args and args[0].startswith('--')):
        Path(args[0] if args else OUT).write_text(generate())
        return 0
    else:
        sys.exit(__doc__)
    OUT.write_text(generate())
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
