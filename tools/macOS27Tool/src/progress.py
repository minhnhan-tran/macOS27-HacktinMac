"""Backend commands seen through the console: their output goes to a log file, the screen gets steps in plain words.

m27's backends (golden-run --stage-kit, golden-assemble.sh and golden-image.py, the stick writer) print what they do
for developers: commands, JSON dumps, diskutil chatter. Runner runs such a command with its output going to the log
file only, and renders what it understands:

  STEP NAME                  golden-image.py's steps (LAYERS, "recipe NAME", "overlay NAME", "post NAME") and
                             golden-pristine.sh's sub-steps, as numbered phases and one line per step
  + COMMAND                  golden-run's commands while it stages a kit, as what is being prepared
  @m27 {JSON}                an event any backend may print (the interface agreed with builder):
                               {"ev": "step", "name": NAME}                         as STEP NAME
                               {"ev": "progress", "done": N, "total": M, "unit": "bytes"|"files"|"%"}
                               {"ev": "item", "text": TEXT, "ok": true, "level": "steps"}
                               {"ev": "note", "text": TEXT}
                               {"ev": "stop", "what": ..., "why": ..., "cause": ..., "options": [...]}

Runner(c, command) is called like subprocess.call(argv) and returns the exit status, so a backend that takes a run=
function takes it unchanged. On a terminal the current step has a spinner and its time, redrawn in place; elsewhere a
"still working" line comes every 30 s. At --quiet the install is one bar with the phase's name.
"""
import collections
import datetime
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

# The install's phases (variation 5's [N/7]), each with golden-image.py's steps that belong to it, in order.
Phase = collections.namedtuple('Phase', 'title note steps seconds')
INSTALL = (
    Phase('Lay out the disk', None, ('layout',), 120),
    Phase('Copy macOS 27', None, ('golden-system', 'group-volumes'), 380),
    Phase('Add Intel support', None, ('golden-finalize', 'boot-and-esp', 'hardware'), 170),
    Phase('Link the kernel', 'Tahoe kmutil', ('recipe darwin27-kernel',), 155),
    Phase('Apply packs', 'what each pack brings, from the kit', ('recipe', 'overlay'), 1615),
    Phase('Seal', None, ('snapshot',), 65),
    Phase('Make it bootable', None, ('posts', 'post', 'efi-bundle'), 35),
)
# seconds: the 2026-11-16 builder VM assembly (golden-26B131-20261116-111912); only the --quiet bar's pace uses them.

# What each step does, in the words the screen uses (internal names stay in the log).
SAY = {
    'tools': 'copy the installer\'s tools and Python',
    'layout': 'GPT partition map and an APFS container',
    'golden-system': 'the System volume, from the installer\'s filesystem image',
    'group-volumes': 'Data, Preboot, Recovery and VM volumes',
    'golden-finalize': 'Tahoe\'s Intel drivers, the Data template and firmlinks',
    'golden-data': 'the Data volume template',
    'golden-firmlinks': 'firmlinks',
    'tahoe-extensions': 'Tahoe\'s Intel kernel extensions',
    'intel-kdk': 'Intel kernel extensions from the KDK',
    'darwin27-kernel-file': 'the Darwin 27 kernel',
    'tahoe-native': 'Tahoe\'s Intel system programs',
    'boot-and-esp': 'Intel boot files and the EFI partition',
    'hardware': 'OCLP\'s legacy hardware support: graphics, Wi-Fi, audio',
    'recipe darwin27-kernel': 'kernel collections linked with Tahoe\'s kmutil',
    'recipe golden-gate-userspace': 'build the shared cache (the longest step)',
    'recipe golden-gate-native-identities': 'Intel identities for Apple\'s programs',
    'recipe macos27tool-runtime': 'm27, which runs ARM-only apps',
    'snapshot': 'update Preboot, bless, take the system snapshot',
    'posts': 'settings, caches and the OpenCore configuration',
    'efi-bundle': 'unmount the new volumes',
}
STAGE = (   # golden-run --stage-kit's "+ COMMAND" lines, in the words the screen uses
    (r'inputs\.sh', 'every input checked against its sha256'),
    (r'minimal-native', 'Tahoe\'s Intel system programs'),
    (r'minimal-extensions', 'Tahoe\'s Intel kernel extensions'),
    (r'minimal-boot', 'Intel boot files'),
    (r'minimal-driverkit', 'Tahoe\'s DriverKit drivers'),
    (r'minimal-recovery', 'Recovery files'),
    (r'oclp-hardware', 'OCLP\'s legacy hardware files'),
    (r'build-efi\.py', 'OpenCore for this Mac'),
    (r'metallib|shader', 'Metal libraries'),
)
ERROR = re.compile(r'(error|failed|refused|exception|cannot|could not|not found|no such)', re.I)
NOISE = re.compile(r'^(\+ |INFO:|\{|\[|Volume |Started |Finished |Waiting |Unmounting|Mounting)')


def output_log(command, now=None, env=None):
    """Where COMMAND's backend output goes: the stick's logs folder when run from the stick (a Recovery's own disk
    is gone after a restart), else the command logs folder (src/report.py)."""
    env = os.environ if env is None else env
    stamp = (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
    if env.get('M27_STICK'):
        folder = Path(env['M27_STICK']) / 'logs'
    else:
        import report
        folder = report.log_dir(env)
    return folder / ('%s-%s.log' % (command, stamp))


def tree_size(path, seen=None, shared=None):
    """(files, bytes) under PATH, symlinks not followed; (0, 0) when it is not there. A hard link's bytes count once;
    with SHARED (a set), a file under inputs/ counts once across calls (recipes share input rows, and the USB
    installer carries each once)."""
    files = size = 0
    seen = set() if seen is None else seen
    base = str(path)
    for root, dirs, names in os.walk(base):
        for n in names:
            full = os.path.join(root, n)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            files += 1
            if st.st_nlink > 1:
                if (st.st_dev, st.st_ino) in seen:
                    continue
                seen.add((st.st_dev, st.st_ino))
            if shared is not None:
                rel = os.path.relpath(full, base)
                if rel.startswith('inputs' + os.sep):
                    key = (rel, st.st_size)
                    if key in shared:
                        continue
                    shared.add(key)
            size += st.st_size
    return files, size


def used_bytes(path):
    """Bytes in use on the volume mounted at PATH, or None when nothing is mounted there."""
    try:
        if not os.path.ismount(str(path)):
            return None
        st = os.statvfs(str(path))
    except OSError:
        return None
    return (st.f_blocks - st.f_bfree) * st.f_frsize


def recipe_folders(share):
    """{recipe: (files, bytes)} of a kit's hooks/NN-NAME and post/NN-NAME folders (what each recipe brings); an input
    row two recipes share counts for the first."""
    out, seen, shared = {}, set(), set()
    for sub in ('hooks', 'post'):
        base = Path(share) / sub
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and '-' in d.name:
                name = d.name.split('-', 1)[1]
                f, b = tree_size(d, seen, shared)
                of, ob = out.get(name, (0, 0))
                out[name] = (of + f, ob + b)
    return out


def pack_line(c, pack, files, size, extra=''):
    """The mockups' pack line: P(pack, 24), then its files (the size is in the events and the log)."""
    return '%-24s%s files%s' % (pack_name(pack), format(files, ','), extra)


TOP = Path(__file__).resolve().parents[3]
_DECLARED = {}


def declared(recipe, top=None):
    """[(path, origin, spec or None)] the recipe declares (patch.json "files", pipeline/patches.py), [] when none."""
    top = Path(top or TOP)
    if top not in _DECLARED:
        import contextlib
        import io
        import runpy
        by = {}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                patches = runpy.run_path(str(top / 'pipeline' / 'patches.py'))
                rows = patches['declared_files'](str(top / 'patches'))
                spec = runpy.run_path(str(top / 'tools' / 'edit-engine' / 'src' / 'spec.py'))
        except (OSError, ValueError, KeyError, SyntaxError, SystemExit):
            rows, spec = [], None
        for name, rev, _, path, origin, sp, entry in rows:
            by.setdefault(name, []).append((path, origin, sp, rev))
        _DECLARED[top] = (by, spec)
    return _DECLARED[top][0].get(recipe, []), _DECLARED[top][1]


ORIGIN_KIND = {'apple': 'apple', 'ventura': 'apple', 'tahoe': 'tahoe', 'oclp': 'oclp', 'm27': 'm27'}


def edit_nodes(c, recipe, top=None):
    """[(label, [children])] for each file RECIPE declares: origin, every edit (bytes, asm, why) and signing; then
    one node for its whole files. Labels carry the palette's colours."""
    rows, S = declared(recipe, top)
    if not rows or S is None:
        return []
    out = []
    for path, origin, sp, rev in rows:
        if not sp:
            continue
        o = sp['origin']
        kids = [('origin  ' + c.paint(ORIGIN_KIND.get(o.get('kind'), 'dim'), S['origin_text'](o)), [])]
        n = len(sp['edits'])
        for i, e in enumerate(sp['edits'], 1):
            detail = []
            if e['kind'] == 'literal':
                before, after = S['hexbytes'](e['find']), S['hexbytes'](e['replace'])
                detail.append(('bytes  ' + ' '.join(c.paint('bad', '%02X' % b) if j < len(after) and after[j] != b
                                                    else '%02X' % b for j, b in enumerate(before)), []))
                detail.append(('   →   ' + ' '.join(c.paint('ok', '%02X' % b) if j < len(before) and before[j] != b
                                                    else '%02X' % b for j, b in enumerate(after)), []))
            if e.get('asm'):
                detail.append(('asm    %s  →  %s' % (e['asm']['before'], e['asm']['after']), []))
            detail.append(('why    ' + c.paint('dim', e['reason']), []))
            kids.append(('edit %d/%d  %s  %s' % (i, n, c.paint('bold', e['id']), c.paint('dim', S['edit_summary'](e))),
                         detail))
        kids.append(('sign    ' + S['sign_text'](sp.get('sign')), []))
        out.append((c.paint('bar', path), kids))
    whole = [o for _, o, sp, _ in rows if not sp]
    if whole:
        kinds = collections.Counter(S['origin_text'](o) for o in whole)
        out.append((c.paint('dim', '%d whole file%s from %s' % (len(whole), '' if len(whole) == 1 else 's', ', '.join(
            k + (' ×%d' % v if v > 1 else '') for k, v in kinds.most_common()))), []))
    return out


def show_edits(c, recipe, top=None, pack=None):
    """--show-edits: RECIPE's declared files and every edit to them (the mockups' level 4); a tree on a Rich
    terminal (recipe -> file -> edit -> bytes, asm, why), else indented lines."""
    nodes = edit_nodes(c, recipe, top) if c.at('edits') else []
    if not nodes:
        return
    plain = []

    def flat(items, depth):
        for label, kids in items:
            plain.append('  ' + '    ' * depth + label)
            flat(kids, depth + 1)
    plain.append('')
    plain.append('  ' + c.paint('bold', recipe) + ('  ' + c.paint('dim', pack_name(pack)) if pack else ''))
    flat(nodes, 1)

    def tree():
        from rich.text import Text
        from rich.tree import Tree
        from rich.padding import Padding
        root = Tree(Text.from_ansi(c.paint('bold', recipe) + ('  ' + c.paint('dim', pack_name(pack)) if pack else '')),
                    guide_style=c.style('dim'))

        def add(node, items):
            for label, kids in items:
                add(node.add(Text.from_ansi(label)), kids)
        add(root, nodes)
        return Padding(root, (1, 0, 0, 2))
    c.block(tree, plain, 'edits')


def pack_table(c, rows):
    """The packs applied: a table on a Rich terminal (pack, recipes, files, size, edits, ✓), else one line each."""
    import console
    plain = [console.at_column('  ' + (pack_line(c, pack, files, size) if files else '%-24s%d recipe%s' % (
        pack_name(pack), len(names), '' if len(names) == 1 else 's')), c.col()) + c.ok
        for pack, names, files, size, edits in rows]

    def table():
        from rich.table import Table
        from rich import box
        t = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, padding=(0, 2), header_style=c.style('dim'))
        for name, just in (('pack', 'left'), ('recipes', 'right'), ('files', 'right'), ('size', 'right'),
                           ('edits', 'right'), ('', 'left')):
            t.add_column(name, justify=just, no_wrap=True)
        for pack, names, files, size, edits in rows:
            t.add_row(pack_name(pack), str(len(names)), format(files, ',') if files else '',
                      console.human_size(size) if files else '', format(edits, ',') if edits else '',
                      console.Text_ok(c))
        from rich.padding import Padding
        return Padding(t, (0, 0, 0, 2))
    c.block(table, plain)


def pack_name(pack):
    return 'other fixes' if pack in (None, '', '(no pack)') else pack


class Runner:
    """run(argv) -> exit status, like subprocess.call; see the module text."""

    def __init__(self, c, command, groups=None, log=None, popen=subprocess.Popen, env=None):
        self.c, self.command, self.popen = c, command, popen
        self.groups = groups or {}
        self.log = Path(log) if log else None
        self.env = env
        self.tail = collections.deque(maxlen=40)
        self.error = None            # the last line that reads like an error
        self.stop = None             # an "@m27 stop" event's fields
        self.summary = {}            # what the last line counts: files from packs, kernel repairs
        self.asm = None
        self.view = None

    def pack_of(self, recipe):
        for pack, names in self.groups.items():
            if recipe in names:
                return pack
        return None

    def logfile(self):
        """The backend log: appended, never through a symlink, 0600 in a 0700 folder; when that folder cannot be
        used, a new private folder of this run (never a shared name in /tmp)."""
        import safety as SAFE
        import tempfile
        if self.log is None:
            self.log = output_log(self.command)
        try:
            return SAFE.open_log(self.log)                       # in its pinned folder (SAFE.Folder.open_log)
        except OSError:
            self.log = Path(tempfile.mkdtemp(prefix='m27-logs-')) / self.log.name
            return SAFE.open_log(self.log)

    def view_for(self, argv):
        words = [str(x) for x in argv]
        if any(w.endswith('golden-assemble.sh') for w in words):
            at = next(i for i, w in enumerate(words) if w.endswith('golden-assemble.sh'))
            return Assembly(self, kit=words[at + 1] if len(words) > at + 1 else None,
                            local=words[at + 2] if len(words) > at + 2 else None)
        if '--stage-kit' in words:
            return Kit(self, words)
        if '--collect-kit' in words:
            return Single(self, 'keep the sealed system images for the next build', level='files')
        return Single(self, ' '.join(Path(words[0]).name.split() + words[1:2]), level='files')

    def __call__(self, argv, **kw):
        c = self.c
        self.view = view = self.view_for(argv)
        out = self.logfile()
        c.event('backend', argv=[str(x) for x in argv], log=str(self.log))
        out.write('\n### %s  %s\n' % (datetime.datetime.now().isoformat(timespec='seconds'),
                                      ' '.join(str(x) for x in argv)))
        out.flush()
        base = kw.pop('env', None) or (self.env if self.env is not None else os.environ)   # a caller's env wins
        env = dict(base, M27_EVENTS='1', PYTHONUNBUFFERED='1')
        view.start()
        try:
            p = self.popen([str(x) for x in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **kw)
        except OSError as x:
            out.write('cannot start: %s\n' % x)
            out.close()
            self.error = str(x)
            view.end(127)
            return 127
        lines = queue.Queue()

        def read():
            for raw in iter(p.stdout.readline, b''):
                lines.put(raw.decode('utf-8', errors='replace'))
            lines.put(None)
        threading.Thread(target=read, daemon=True).start()
        ticked = time.monotonic()
        try:
            while True:
                try:
                    line = lines.get(timeout=1)
                except queue.Empty:
                    line = ''
                if time.monotonic() - ticked >= 1:
                    ticked = time.monotonic()
                    view.tick()
                if line is None:
                    break
                if not line:
                    continue
                out.write(line)
                out.flush()
                self.feed(view, line.rstrip('\n'))
            rc = p.wait()
        except KeyboardInterrupt:
            p.terminate()
            rc = p.wait()
            view.end(rc or 130)
            out.close()
            raise
        out.write('### exit %d\n' % rc)
        out.close()
        view.end(rc)
        return rc

    def feed(self, view, line):
        import safety as SAFE
        text = SAFE.text(line.strip())                  # external text: no control sequences reach the terminal
        if text.startswith('@m27 '):
            try:
                ev = SAFE.clean(json.loads(line.strip()[5:]))    # every decoded string shown as text
            except (ValueError, RecursionError):
                return
            if not isinstance(ev, dict):
                return
            kind = ev.get('ev')
            if kind == 'step':
                view.step(str(ev.get('name', '')))
            elif kind == 'progress':
                view.progress(ev.get('done'), ev.get('total'), ev.get('unit'))
            elif kind == 'item':
                self.c.item(SAFE.text(ev.get('text', '')), ev.get('level', 'steps'), ok=ev.get('ok'))
            elif kind == 'note':
                self.c.note(SAFE.text(ev.get('text', '')))
            elif kind == 'stop':
                self.stop = ev                                   # already cleaned when decoded
            return
        if text.startswith('STEP '):
            view.step(text[5:].strip())
            return
        if text.startswith('RESUME at '):
            view.resumed = True
            return
        if text.startswith('+ '):
            view.command(text[2:])
        elif text:
            view.line(text)
        if text:
            self.tail.append(text)
            if ERROR.search(text) and not text.startswith(('+ ', 'RESULT rc=0')):
                self.error = text

    def explain(self, x):
        """Fill a Stopped report from what the backend said: its own stop event, else its last error line, and the
        log's path."""
        if self.stop:
            for k in ('what', 'why', 'cause', 'untouched'):
                if self.stop.get(k):
                    setattr(x, k, self.stop[k])
            x.options = list(self.stop.get('options') or []) + [o for o in x.options if o not in
                                                                 (self.stop.get('options') or [])]
        elif not x.cause and self.error:
            x.cause = 'the last message was: ' + self.error[:200]
        if self.log and not x.log:
            x.log = str(self.log)
        seen = getattr(self.view, 'seen', None)
        if seen is not None and not x.untouched:
            disk = re.search(r'/dev/(disk\d+)', x.what or '')
            later = 'This Mac will not start from %s yet.' % (disk.group(1) if disk else 'that disk')
            x.untouched = ('before anything was written to the EFI partition. ' if 'boot-and-esp' not in seen
                           else 'after the EFI partition was written. ') + later
        return x


def download_view(c, acquire, origin_of=lambda row: ''):
    """ACQUIRE (tools/acquire's functions) with a fetch that shows each download as a line with its bar, size and
    "sha256 ✓" (the mockups' Download step); the events still reach the caller's on_event, except the byte counts,
    which only the bar shows."""
    fetch = acquire['fetch']

    def shown(rows, cache, *args, on_event=None, **kw):
        by_name = {r['name']: r for r in rows}
        live = {}
        forward = on_event or (lambda ev: None)

        def label(name):
            return '%s %-44s' % (c.paint.origin(origin_of(by_name.get(name, {})), 6), name)

        def on(ev):
            k, name = ev.get('ev'), ev.get('name')
            if k == 'progress':
                t = live.get(name)
                if t is None:
                    t = live[name] = c.task('  ' + label(name), total=ev.get('size') or by_name.get(name, {}).get('size'),
                                            unit='bytes', name=name)
                t.update(done=ev.get('done'))
                return
            if k == 'ok':
                t = live.pop(name, None) or c.task('  ' + label(name), total=by_name.get(name, {}).get('size'),
                                                   unit='bytes', name=name)
                t.final_note = c.paint('ok', 'sha256 ') + c.ok
                t.end(None)
            elif k == 'reused':
                c.item('%s %s' % (label(name), c.paint('dim', 'already downloaded, sha256')), ok=True)
            elif k == 'streamed':
                c.item('%s %s' % (label(name), c.paint('dim', 'read in parts as it is used')), ok=True)
            elif k == 'mirror-failed':
                t = live.pop(name, None)
                if t:
                    t.end(False)
            forward(ev)
        try:
            return fetch(rows, cache, *args, on_event=on, **kw)
        finally:
            for t in live.values():
                t.end(False)
    return dict(acquire, fetch=shown)


class Single:
    """One line for the whole command: a spinner and the time; what it is on now (from STAGE) after the text."""

    def __init__(self, runner, text, labels=(), level='steps'):
        self.r, self.text, self.labels, self.level = runner, text, labels, level
        self.task = None
        self.resumed = False

    def start(self):
        self.task = self.r.c.task('  ' + self.text, self.level)

    def command(self, cmd):
        for pattern, say in self.labels:
            if re.search(pattern, cmd):
                self.task.update(tail=say)
                return

    def step(self, name):
        self.task.update(tail=SAY.get(name, ''))

    def line(self, text):
        pass

    def progress(self, done, total, unit):
        self.task.unit = unit
        self.task.update(done=done, total=total)

    def tick(self):
        self.task.update()

    def end(self, rc):
        self.task.tail = ''
        self.task.end(rc == 0)


class Kit:
    """golden-run --stage-kit: one line per part it stages (a bar for each copy), then one line per pack with the
    files and bytes the kit carries for it."""

    def __init__(self, runner, words):
        self.r, self.c = runner, runner.c
        work = words[words.index('--work') + 1] if '--work' in words else None
        self.share = Path(work) / 'share' if work else None
        self.task = None
        self.copy = None           # (source, destination, total bytes) of the copy on screen
        self.resumed = False
        self.packs_shown = False

    def start(self):
        pass

    def begin(self, text, total=None):
        self.finish()
        self.task = self.c.task('  ' + text, total=total, unit='bytes' if total else None)

    def finish(self, ok=True):
        if self.task:
            self.task.end(ok)
            self.task = None
        self.copy = None

    def command(self, cmd):
        import shlex
        try:
            words = shlex.split(cmd)
        except ValueError:
            words = cmd.split()
        say = next((s for pattern, s in STAGE if re.search(pattern, cmd)), None)
        if self.task and getattr(self.task, 'text_of', '') == 'inputs.sh' and not self.packs_shown:
            self.finish()
            self.show_packs()
        if words and words[0] == 'ditto' and len(words) >= 3:
            total = tree_size(words[-2])[1]
            self.begin('%-38s' % ('copy ' + (say or Path(words[-2]).name)), total=total or None)
            self.copy = (words[-2], words[-1])
            return
        if 'inputs.sh' in cmd:
            self.begin('stage each recipe\'s inputs, every one checked against its sha256')
            self.task.text_of = 'inputs.sh'
            return
        self.begin(say or ('prepare ' + Path(words[0]).name if words else 'prepare'))

    def show_packs(self):
        """One line per pack: what the kit carries for its recipes (hooks/ and post/)."""
        self.packs_shown = True
        if not self.share:
            return
        sizes = recipe_folders(self.share)
        packs = collections.OrderedDict()
        for recipe, (f, b) in sizes.items():
            pf, pb = packs.get(self.r.pack_of(recipe), (0, 0))
            packs[self.r.pack_of(recipe)] = (pf + f, pb + b)
        order = list(self.r.groups) + [p for p in packs if p not in self.r.groups]
        rows = []
        for pack in order:
            if pack in packs:
                f, b = packs[pack]
                names = self.r.groups.get(pack, [])
                edits = sum(len(sp['edits']) for n in names for _, _, sp, _ in declared(n)[0] if sp)
                rows.append((pack, names, f, b, edits))
                self.c.event('pack.staged', pack=pack, files=f, bytes=b, edits=edits)
        if rows:
            pack_table(self.c, rows)
        if self.c.at('edits'):
            for pack, names, _, _, _ in rows:
                for recipe in names:
                    show_edits(self.c, recipe, pack=pack)

    def step(self, name):
        pass

    def line(self, text):
        if text.startswith('Kit staged:'):
            self.finish()

    def progress(self, done, total, unit):
        if self.task:
            self.task.unit = unit
            self.task.update(done=done, total=total)

    def tick(self):
        if self.task and self.copy:
            self.task.update(done=tree_size(self.copy[1])[1])
        elif self.task:
            self.task.update()

    def end(self, rc):
        if rc == 0 and not self.packs_shown:
            self.finish()
            self.show_packs()
        self.finish(rc == 0)


class Assembly:
    """golden-assemble.sh: the seven phases, one line per step, one line per pack, the --quiet bar."""

    def __init__(self, runner, phases=INSTALL, kit=None, local=None):
        self.r, self.c, self.phases = runner, runner.c, phases
        self.kit = Path(kit) if kit else None
        self.local = Path(local) if local else None
        self.seen = set()          # the steps that ran (for "what was left untouched")
        self.copy_source = None    # the installer's filesystem image, mounted (for the System copy's bar)
        self.copy_base = None
        self.sizes = None          # {recipe: (files, bytes)} the kit carries, read when Apply packs starts
        self.index = 0             # the phase being worked on, 1-based; 0 before the first
        self.task = None
        self.resumed = False
        self.packs = collections.OrderedDict()   # pack -> recipes applied in this phase
        self.posts = []
        self.recipe_line = False   # the line on screen is one recipe's
        self.last = None
        self.bar = None
        self.phase_started = time.monotonic()

    # -- phases
    def phase_of(self, name):
        kind = name.split(' ', 1)[0]
        for i, ph in enumerate(self.phases, 1):
            if name in ph.steps:
                return i
        for i, ph in enumerate(self.phases, 1):
            if kind in ph.steps and ' ' in name:
                return i
        return None

    def enter(self, n):
        c = self.c
        while self.index < n:
            self.leave()
            self.index += 1
            ph = self.phases[self.index - 1]
            note = 'done in the earlier attempt' if self.index < n and self.resumed else self.note(self.index, ph)
            c.step(ph.title, note=note, quiet=False)
            self.phase_started = time.monotonic()
            self.pace()

    def note(self, n, ph):
        """The header's subtitle: the origin of what the phase copies (the mockups' "origin: Apple 26B131")."""
        asm = getattr(self.r, 'asm', None) or {}
        c = self.c
        if n == 2 and asm.get('golden_build'):
            return c.paint('dim', 'origin:') + ' ' + c.paint('apple', 'Apple ' + asm['golden_build'])
        if n == 3 and asm.get('donor_build'):
            return c.paint('dim', 'origin:') + ' ' + c.paint('tahoe', 'Apple %s (Tahoe)' % asm['donor_build']) + \
                ' · ' + c.paint('oclp', 'OCLP')
        return ph.note

    def finish(self, ok=True):
        """End the line on screen; a recipe's line is not kept (its pack's line follows)."""
        t, self.task = self.task, None
        if t is None:
            return
        if self.recipe_line and ok and not self.c.at('files'):
            t.drop()
        else:
            t.end(ok)

    def flush(self):
        """The closing lines of the recipes so far: one per pack (Apply packs), one for the post recipes."""
        self.finish()
        if self.packs and self.sizes is None:
            self.sizes = recipe_folders(self.kit) if self.kit and self.kit.is_dir() else {}
        rows = []
        for pack, names in self.packs.items():
            edits = sum(len(sp['edits']) for n in names for _, _, sp, _ in declared(n)[0] if sp)
            self.r.summary['edits'] = self.r.summary.get('edits', 0) + edits
            files = sum(self.sizes.get(n, (0, 0))[0] for n in names)
            size = sum(self.sizes.get(n, (0, 0))[1] for n in names)
            rows.append((pack, names, files, size, edits))
            self.c.event('pack.applied', pack=pack, recipes=names, files=files, bytes=size, edits=edits)
            self.r.summary['files'] = self.r.summary.get('files', 0) + files
        if rows:
            pack_table(self.c, rows)
        self.packs = collections.OrderedDict()
        if self.posts:
            self.c.item('%s  %d recipes' % (SAY['posts'], len(self.posts)), ok=True, ev='posts.applied',
                        recipes=self.posts)
            self.posts = []

    def leave(self):
        self.flush()

    # -- the footer: the overall phase, a bar, the time so far and about how long is left
    def fraction(self):
        total = float(sum(p.seconds for p in self.phases))
        before = sum(p.seconds for p in self.phases[:max(self.index - 1, 0)])
        ph = self.phases[self.index - 1] if self.index else None
        within = min(0.95, (time.monotonic() - self.phase_started) / float(ph.seconds)) * ph.seconds if ph else 0
        return (before + within) / total

    def footer(self):
        import console
        c = self.c
        if not self.index:
            return ''
        ph = self.phases[self.index - 1]
        f = self.fraction()
        spent = time.monotonic() - self.began
        left = ''
        if spent > 120 and f > 0.05:
            left = ' · about %s left' % console.elapsed(max(60, spent / f - spent))
        width = 24
        full = int(round(f * width))
        bar = c.paint('bar', '━' * full) + c.paint('dim', ('╺' if full < width else '') + '━' * max(0, width - full - 1))
        return '%s %s  %s %3d%%  %s' % (c.paint('dim', '─'), c.paint('bold', '[%d/%d] %s' % (self.index, len(self.phases), ph.title)),
                                       bar, int(100 * f), c.paint('dim', console.elapsed(spent) + left))

    # -- the --quiet bar
    def pace(self):
        if self.c.level != 'quiet':
            return
        total = float(sum(p.seconds for p in self.phases))
        before = sum(p.seconds for p in self.phases[:max(self.index - 1, 0)])
        ph = self.phases[self.index - 1] if self.index else None
        within = 0
        if ph:
            within = min(0.95, (time.monotonic() - self.phase_started) / float(ph.seconds)) * ph.seconds
        title = ph.title.lower() if ph else 'preparing'
        if self.bar is None:
            self.bar = self.c.task('Installing macOS 27', 'quiet', total=1000, unit='%')
            self.bar.width, self.bar.bar_at = 30, 0       # the mockup: "Installing macOS 27  ████ 42%  Copying macOS 27…"
        self.bar.update(done=int(1000 * (before + within) / total), tail=title[0].upper() + title[1:] + '…')

    # -- the backend's lines
    def start(self):
        self.began = time.monotonic()
        if self.c.rich and self.c.level != 'quiet':
            self.c.footer = self.footer
        self.c.steps(len(self.phases))
        self.enter(1)
        self.task = self.c.task('  ' + SAY['tools'])

    def step(self, name):
        self.seen.add(name)
        if name == self.last:          # golden-pristine.sh names the step golden-image.py has just started
            return
        self.last = name
        n = self.phase_of(name)
        if n is not None and n > self.index:
            self.enter(n)
        elif n is None and name in SAY and self.task and not self.recipe_line:
            self.task.update(tail=SAY[name])     # a sub-step of the line on screen (golden-pristine.sh's)
            return
        if name in ('recipes', 'posts'):
            return
        kind, _, recipe = name.partition(' ')
        if recipe and kind in ('recipe', 'overlay', 'post') and (self.index >= 5 or kind == 'post'):
            self.recipe(kind, recipe)
            return
        self.flush()
        self.recipe_line = False
        self.task = self.c.task('  ' + (SAY.get(name) or name))

    def recipe(self, kind, recipe):
        """One recipe of Apply packs, or a post recipe: its line shows its pack (Apply packs) or what posts do;
        the closing line per pack comes when the phase ends."""
        self.finish()
        self.recipe_line = True
        if self.c.at('files'):
            label = '%s %s' % (self.c.paint('bold', '%-22s' % pack_name(self.r.pack_of(recipe))), recipe)
        elif kind == 'post':
            label = '%s  %s' % (SAY['posts'], 'recipe %d' % (len(self.posts) + 1))
        else:
            label = '%s %s' % (self.c.paint('bold', '%-22s' % pack_name(self.r.pack_of(recipe))),
                               SAY.get('recipe ' + recipe, 'recipe %d' % (sum(map(len, self.packs.values())) + 1)))
        if kind == 'post':
            self.posts.append(recipe)
        else:
            self.packs.setdefault(self.r.pack_of(recipe), []).append(recipe)
        if self.c.at('edits'):
            show_edits(self.c, recipe, pack=self.r.pack_of(recipe))
        self.task = self.c.task(('    ' if self.c.at('files') else '  ') + label)

    def command(self, cmd):
        pass

    KC = re.compile(r'^Linked the Darwin 27 kernel \((\w+)\): Boot ([0-9a-f]{8,}) and System collections')
    REPAIRS = re.compile(r'^Installed (\d+) recorded kernel repairs')
    STAMP = re.compile(r'^stamped .*M27Exec\.kext for kernel ([0-9a-f]{8,})')

    ATTACHED = re.compile(r'^/dev/disk\d+s\d+\s+\S+\s+(/\S+/filesystem)$')

    def line(self, text):
        m = self.ATTACHED.match(text)
        if m:
            self.copy_source = m.group(1)          # hdiutil attach of the System's source, golden-pristine.sh
            return
        m = self.KC.match(text)
        if m:
            how, boot = m.groups()
            proven = self.proven_boot()
            if proven and proven.startswith(boot):
                tail = self.c.paint('ok', 'matches the proven build')
            elif proven:
                tail = self.c.paint('warn', 'differs from the proven build')
            else:
                tail = self.c.paint('dim', 'linked here' if how == 'built' else how)
            self.c.event('kc.link', how=how, boot=boot, proven=proven)
            if self.task and not self.recipe_line:
                self.task.update(text='  BootKC and SystemKC linked, Boot %s…' % boot[:12])
                self.task.final_note = tail
            else:
                self.say('BootKC %s…  %s' % (boot[:12], tail))
            return
        m = self.REPAIRS.match(text)
        if m:
            self.r.summary['repairs'] = int(m.group(1))
            if not self.bind_detail(int(m.group(1))):
                self.say('%s kernel repairs bound to this kernel, IntelCommpage added' % m.group(1), ok=True,
                         ev='kc.bind', repairs=int(m.group(1)))
            return
        m = self.STAMP.match(text)
        if m:
            self.c.item('M27Exec stamped for kernel %s…' % m.group(1)[:12], 'files', ok=True, ev='kc.stamp',
                        kernel=m.group(1))

    def bind_detail(self, repairs):
        """The binder's record (post macos27tool-opencore's kc-binder.json): the mockups' "bind N kernel repairs" line, what
        it wrote, and at --show-edits each site it bound. False when there is no record to read."""
        if not self.local:
            return False
        try:
            rec = json.loads((self.local / 'work' / 'post' / 'macos27tool-opencore' / 'kc-binder.json').read_text())
        except (OSError, ValueError):
            return False
        c = self.c
        patches, points = rec.get('patches', []), rec.get('points', {})
        c.item('bind  %d kernel repairs against the BootKC just linked' % repairs, ok=True, ev='kc.bind',
               repairs=repairs, mode=rec.get('mode'), patches=len(patches))
        table = rec.get('commpage_table') or {}
        c.print('        ' + c.paint('dim', '→ %d OpenCore patches written · M27Exec stamped · IntelCommpage table %s' % (
            len(patches), 'filled' if table.get('load_time') or table.get('sha256') else 'checked')))
        for name in ('site', 'range_call', 'pv_hook', 'pv_cave'):
            pt = points.get(name)
            if not pt:
                continue
            what = '%d fields' % pt['fields'] if pt.get('fields') else '%s bytes free' % pt.get('capacity', '?')
            c.print('    %-24s%-20s%-12s%s' % ('early-vm.' + name, '%s +%s' % (pt['segment'], pt['offset']),
                                              '%s bytes' % pt.get('length', pt.get('tail', '?')), what) +
                    '  ' + c.ok, 'edits')
        for key, pt in points.items():
            if '.' in key:
                c.print('      %-22s%-28s%s' % (key.split('.', 1)[1], '%s +%s' % (pt['segment'], pt['offset']), c.ok),
                        'edits')
        for pch in patches:
            c.print('    %s %s' % (c.paint('dim', 'patch'), '%s  (%s bytes at %s)' % (pch['comment'], pch['length'],
                                                                                    pch['offset'])), 'edits')
        return True

    def say(self, text, ok=None, **fields):
        """A line under the step on screen, printed above its live line."""
        self.c.item(text, ok=ok, **fields)

    def proven_boot(self):
        """The BootKC sha256 the kit's prelinked collections were recorded with, or None (a kit linked on the target)."""
        if not self.kit:
            return None
        for found in sorted(self.kit.glob('hooks/*darwin27-kernel/prelinked/prelinked.json')):
            try:
                return json.loads(found.read_text())['files']['BootKernelExtensions.kc']
            except (OSError, ValueError, KeyError):
                return None
        return None

    def progress(self, done, total, unit):
        if self.task:
            self.task.unit = unit
            self.task.update(done=done, total=total)

    def tick(self):
        if self.task and self.last == 'golden-system' and self.copy_source and self.local:
            self.copy_progress()
        elif self.task:
            self.task.update()
        self.pace()

    def copy_progress(self):
        """The System copy as a bar: bytes on the new System volume against the source image's."""
        total = used_bytes(self.copy_source)
        done = used_bytes(self.local / 'work' / 'mnt' / 'system')
        if total is None or done is None:
            self.task.update()
            return
        if self.copy_base is None:
            self.copy_base = done
        self.task.unit = 'bytes'
        self.task.update(done=max(0, min(done - self.copy_base, total)), total=total)

    def end(self, rc):
        self.c.footer = None
        if rc == 0:
            self.flush()
            if self.bar:
                self.bar.update(done=1000)
                self.bar.final_note = 'Done'
                self.bar.end(None, timed=False)
            return
        import console
        room = max(20, self.c.width() - self.c.col() - 5) if self.c.animate else 56
        why = self.c.paint('bad', console.fit(self.r.error, room)) if self.r.error else ''
        if self.task is None and self.c.at('steps'):
            # it stopped between lines (in a step that has no line of its own): mark that step
            import console
            self.c.print(console.at_column('  ' + (SAY.get(self.last) or self.last or 'this step'), self.c.col()) +
                         self.c.bad + ('  ' + why if why else ''))
        elif self.task is not None:
            self.task.final_note = why or None
        self.finish(False)
        if self.bar:
            self.bar.end(False)
