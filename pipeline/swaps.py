#!/usr/bin/env python3.14
"""swaps: no fix proven by a live swap on the laptop may be lost at the next image push.

  swaps.py status [--ledger FILE]      every swapped laptop path, newest swap first, with its resolution
  swaps.py check  [--ledger FILE]      exit 1 when a path's newest swap has no resolution (macOS27Tool build-image runs it)
  swaps.py proven PATH --evidence TEXT [--sha SHA] [--who WHO] [--ledger FILE]
                                       record that the swap of PATH (its newest, or SHA) is proven: TEXT is
                                       "bench ITEM-OR-EVIDENCE" or "owner: WORDS"
  swaps.py gate   [--ledger FILE]      exit 1 when a proven swap has no resolution (golden-run and release-cut run it)

The laptop swap tool records each put on this Mac (ledger: date, laptop path, sha256, mode, local source, who; the
bytes under objects/<sha256> beside it). --ledger, HAO_SWAP_LEDGER, or the path on the first line of
~/.config/hao/swap-ledger (a local file, never tracked) names it; with none of them there is no ledger. Only a
path's newest swap needs a resolution: earlier ones were iterations it replaced.

A proven swap is one a bench pass or the owner's word showed to work. The ledger records the moment: a line whose mode
is "proven" (date, path, sha256 of the proven swap, proven, evidence, who, laptop), written by `swaps.py proven`.
Every proven swap needs its own resolution, whatever was swapped at that path afterwards, and until it has one
golden-run and release-cut refuse to start a build (owner, 2026-11-14: a proven fix that "was never added to something
or never converted to a build ... That's nonsense"). There is no override: resolve it, or mark it "dropped: WHY".

A swap is resolved when:
  - pipeline/live-swaps.tsv, or the private file outside the repository (HAO_SWAP_PRIVATE, default
    ~/.config/hao/live-swaps-private.tsv), has a line "SHA256<TAB>LAPTOP PATH<TAB>RESOLUTION" for it (a path on the EFI partition as
    the ledger names it: "ESP diskNsM EFI/..."), RESOLUTION being
    "row NAME" or "recipe NAME" (the fix now ships in images that way) or "dropped: WHY" (not wanted), or
  - its path is under /usr/lib/hao/ and the hao_runtime row's payload holds the same bytes at root/<path> (macos27tool-runtime
    fixes ship in its payload).
"""
import argparse
import hashlib
import os
from pathlib import Path
import runpy
import sys

TOP = Path(__file__).resolve().parents[1]


def default_ledger():
    """HAO_SWAP_LEDGER, else the path on the first line of ~/.config/hao/swap-ledger, else
    ~/.config/hao/swap-ledger.tsv (absent: no ledger)."""
    env = os.environ.get('HAO_SWAP_LEDGER')
    if env:
        return Path(env)
    conf = Path.home() / '.config/hao/swap-ledger'
    first = conf.read_text().strip().splitlines()[:1] if conf.is_file() else []
    return Path(os.path.expanduser(first[0])) if first and first[0].strip() else conf.with_name('swap-ledger.tsv')


LEDGER = default_ledger()
RESOLUTIONS = TOP / 'pipeline' / 'live-swaps.tsv'
PRIVATE = Path(os.environ.get('HAO_SWAP_PRIVATE') or Path.home() / '.config/hao/live-swaps-private.tsv')
RUNTIME_ROWS = (TOP / 'pipeline/inputs/golden/26B131', 'hao_runtime')
KINDS = ('row ', 'recipe ', 'dropped: ')
FIELDS = ('date', 'path', 'sha256', 'mode', 'source', 'who', 'laptop')
PROVEN = 'proven'
EVIDENCE = ('bench ', 'owner: ')


def ledger(path=LEDGER, proofs=False):
    """[{date, path, sha256, mode, source, who, laptop}] in file order (oldest first); [] when there is no ledger.
    The swaps only, or with PROOFS the "proven" lines only (their source is the evidence)."""
    out = []
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text().splitlines():
        f = line.split('\t')
        if len(f) >= 3 and len(f[2]) == 64:
            e = dict(zip(FIELDS, f + [''] * (len(FIELDS) - len(f))))
            if (e['mode'] == PROVEN) == proofs:
                out.append(e)
    return out


def resolutions(path=RESOLUTIONS, private=None):
    """{(sha256, laptop path): resolution} from the tracked file and, for the repository's file, the private one
    (PRIVATE: same format, outside the repository; private=False leaves it out); malformed lines raise."""
    out = {}
    if private is None:
        private = PRIVATE if Path(path) == RESOLUTIONS else False
    if private and Path(private).is_file():
        out.update(resolutions(private, False))
    if not Path(path).is_file():
        return out
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        f = line.split('\t')
        if len(f) != 3 or len(f[0]) != 64 or not f[1].startswith(('/', 'ESP ')) or not f[2].startswith(KINDS):
            raise ValueError('%s:%d: want SHA256<TAB>/LAPTOP/PATH<TAB>row NAME | recipe NAME | dropped: WHY' % (path, n))
        out[(f[0], f[1])] = f[2]
    return out


def _sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def runtime_payload(rows=RUNTIME_ROWS):
    """The hao_runtime row's payload folder, or None when it does not resolve."""
    try:
        return Path(runpy.run_path(str(TOP / 'pipeline' / 'lock.py'))['path'](str(rows[0]), rows[1]))
    except (Exception, SystemExit):         # lock.path exits when the row's folder is not in this store
        return None


def resolution(e, resolved, payload=None):
    """The resolution of the swap E, or None."""
    r = resolved.get((e['sha256'], e['path']))
    if r is None and payload is not None and e['path'].startswith('/usr/lib/hao/'):
        f = payload / 'root' / e['path'].lstrip('/')
        if f.is_file() and _sha(f) == e['sha256']:
            r = 'row hao_runtime (same bytes in its payload)'
    return r


def status(entries, resolved, payload=None, proofs=()):
    """[{path, sha256, date, newest, resolution, proven}] for each path's newest swap (newest first), plus the older
    swaps of that path counted, then every proven swap that is not its path's newest. resolution None means
    unresolved; proven is the proof line, or None."""
    newest, count = {}, {}
    for e in entries:
        newest[e['path']] = e
        count[e['path']] = count.get(e['path'], 0) + 1
    proof = {(p['sha256'], p['path']): p for p in proofs}
    out = [dict(e, swaps=count[p], resolution=resolution(e, resolved, payload), proven=proof.get((e['sha256'], p)))
           for p, e in newest.items()]
    shown = {(r['sha256'], r['path']) for r in out}
    for key, p in proof.items():
        if key in shown:
            continue
        e = next((e for e in reversed(entries) if (e['sha256'], e['path']) == key), None)
        e = e or dict(p, mode='', source='(no swap of these bytes in the ledger)', who='')
        out.append(dict(e, swaps=count.get(e['path'], 0), resolution=resolution(e, resolved, payload), proven=p))
    return sorted(out, key=lambda x: x['date'], reverse=True)


def proven_open(ledger_path=LEDGER, resolutions_path=RESOLUTIONS, payload=None):
    """The proven swaps that have no resolution."""
    rows = status(ledger(ledger_path), resolutions(resolutions_path),
                  payload if payload is not None else runtime_payload(), ledger(ledger_path, proofs=True))
    return [r for r in rows if r['proven'] and r['resolution'] is None]


def gate(ledger_path=LEDGER, resolutions_path=RESOLUTIONS, payload=None):
    """The lines a build prints before refusing to start, or [] when every proven swap is resolved."""
    rows = proven_open(ledger_path, resolutions_path, payload)
    if not rows:
        return []
    lines = ['%d proven live swap%s %s in no recipe or row: a build now would lose %s' % (
        len(rows), 's' if len(rows) > 1 else '', 'are' if len(rows) > 1 else 'is', 'them' if len(rows) > 1 else 'it')]
    for r in rows:
        p = r['proven']
        lines.append('  %s  %s  swapped %s by %s; proven %s by %s: %s' % (
            r['path'], r['sha256'][:12], r['date'], r['who'] or '?', p['date'], p['who'] or '?', p['source']))
    lines.append('resolve each in pipeline/live-swaps.tsv (SHA256<TAB>PATH<TAB>recipe NAME | row NAME | dropped: WHY), '
                 'the recipe or row carrying those bytes; see: python3.14 -I pipeline/swaps.py status')
    return lines


def prove(ledger_path, path, evidence, sha=None, who=None, laptop=''):
    """Append the proven line for PATH's swap (its newest, or SHA). Returns the line."""
    evidence = ' '.join(evidence.split())
    if not evidence.startswith(EVIDENCE) or len(evidence) < 12:
        raise ValueError('evidence must be "bench ITEM-OR-EVIDENCE-PATH" or "owner: THEIR WORDS"')
    swaps = [e for e in ledger(ledger_path) if e['path'] == path]
    if not swaps:
        raise ValueError('no swap of %s in %s' % (path, ledger_path))
    e = next((e for e in reversed(swaps) if e['sha256'] == sha), None) if sha else swaps[-1]
    if e is None:
        raise ValueError('no swap of %s with sha256 %s in %s' % (path, sha, ledger_path))
    import datetime
    who = who or os.environ.get('HAO_SWAP_WHO') or os.environ.get('USER') or '?'
    line = '\t'.join((datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), path, e['sha256'], PROVEN, evidence,
                      who, laptop or e['laptop']))
    with open(ledger_path, 'a') as f:
        f.write(line + '\n')
    return line


def unresolved(ledger_path=LEDGER, resolutions_path=RESOLUTIONS, payload=None):
    rows = status(ledger(ledger_path), resolutions(resolutions_path),
                  payload if payload is not None else runtime_payload(), ledger(ledger_path, proofs=True))
    return [r for r in rows if r['resolution'] is None]


def main(argv):
    ap = argparse.ArgumentParser(prog='swaps.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['status', 'check', 'proven', 'gate'])
    ap.add_argument('path', nargs='?', help='proven: the laptop path')
    ap.add_argument('--ledger', type=Path, default=LEDGER)
    ap.add_argument('--evidence', help='proven: "bench ITEM-OR-EVIDENCE-PATH" or "owner: THEIR WORDS"')
    ap.add_argument('--sha', help='proven: the swap\'s sha256 (default: the path\'s newest swap)')
    ap.add_argument('--who', help='proven: who records it (default HAO_SWAP_WHO, USER)')
    a = ap.parse_args(argv)
    if a.cmd == 'proven':
        if not a.path or not a.evidence:
            ap.error('proven takes PATH and --evidence')
        try:
            print('swaps: recorded ' + prove(a.ledger, a.path, a.evidence, a.sha, a.who).replace('\t', '  '))
        except ValueError as x:
            print('swaps: ' + str(x), file=sys.stderr)
            return 2
        r = [r for r in proven_open(a.ledger) if r['path'] == a.path]
        if r:
            print('swaps: it has no resolution yet: golden-run and release-cut refuse to build until it has one in '
                  'pipeline/live-swaps.tsv')
        return 0
    if a.cmd == 'gate':
        lines = gate(a.ledger)
        for line in lines:
            print('swaps: ' + line, file=sys.stderr)
        return 1 if lines else 0
    rows = status(ledger(a.ledger), resolutions(), runtime_payload(), ledger(a.ledger, proofs=True))
    if a.cmd == 'status':
        if not rows:
            print('swaps: no recorded swaps in %s' % a.ledger)
        for r in rows:
            state = ('PROVEN-OPEN' if r['proven'] else 'OPEN') if r['resolution'] is None else 'resolved'
            print('%s  %-11s %s  %s  (%s)%s%s' % (
                r['date'], state, r['sha256'][:12], r['path'], r['who'] or '?',
                '  proven %s by %s: %s' % (r['proven']['date'], r['proven']['who'], r['proven']['source'])
                if r['proven'] else '', '  -> ' + r['resolution'] if r['resolution'] else ''))
        return 0
    bad = [r for r in rows if r['resolution'] is None]
    for r in bad:
        print('swaps: %s (%s, swapped %s from %s) has no resolution: the fix exists only on the laptop'
              % (r['path'], r['sha256'][:12], r['date'], r['source']), file=sys.stderr)
    if bad:
        print('swaps: put each into a recipe or row, or mark it, in pipeline/live-swaps.tsv '
              '(SHA256<TAB>PATH<TAB>row NAME | recipe NAME | dropped: WHY)', file=sys.stderr)
        return 1
    print('swaps: every swapped laptop path is resolved (%d)' % len(rows))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
