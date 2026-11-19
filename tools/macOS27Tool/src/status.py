"""hao status: one screen about hao on this Mac, read only.

hao's release, where it runs (build Mac, USB installer, installed Mac), macOS and its build, the Mac, SIP and the boot
arguments, the install record and live patches, the ahead-of-time caches, whether an update was checked, and the last
command that stopped. A panel on a terminal, labelled lines in a pipe, one JSON event with --log json.
"""
import datetime
import json
import os
from pathlib import Path

import frontdoor as FD
import macos as MAC
import report as REP

CSR_FS = getattr(MAC, 'CSR_ALLOW_UNRESTRICTED_FS', 0x2)
AOT_DIRS = (Path('/private/var/db/hao'), Path('/usr/lib/hao/aot'))


def _out(host, *argv):
    return REP._out(host, *argv)


BUDGET_FILES, BUDGET_BYTES = 300, 64 << 20


def last_stop(folders):
    """(command, when, what) of the newest command log that ended stopped, or None; ('?', '', why) when it cannot be
    told: a log folder refused (not as expected, or flooded), or the newest BUDGET_FILES logs / BUDGET_BYTES read
    without finding a finished command."""
    import safety as SAFE
    # a folder this user may read but that is not as hao keeps it (or flooded) is said; root's folder unread by a user is
    # normal and not
    refused = [str(f) for f in folders if os.path.isdir(f) and os.access(str(f), os.R_OK | os.X_OK)
               and REP.log_folder(f) is None]
    read = size = 0
    for _, path in REP.candidates(folders):          # newest first; newer logs without an end cannot hide a stop
        if read >= BUDGET_FILES or size >= BUDGET_BYTES:
            return '?', '', 'not found in the newest %d logs' % read
        text = REP.read_log(path)                                    # through its pinned folder
        read, size = read + 1, size + len(text or '')
        try:
            data = json.loads(text) if text is not None else None
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get('events', []), list):
            continue
        ends = [e for e in data.get('events', []) if isinstance(e, dict) and e.get('ev') == 'run.end']
        if ends and ends[-1].get('ok') is False:
            when = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            return SAFE.text(data.get('command')), when, SAFE.text(ends[-1].get('what'))
        if ends:
            break                          # the newest finished command did not stop
    if refused:
        return '?', '', 'log folder %s not read (not as hao keeps it, or too many entries)' % SAFE.text(refused[0])
    return None


def gather(host, env=None, state=REP.STATE, folders=None, aot_dirs=AOT_DIRS):
    """Everything status shows, as a dict (also its --log json event)."""
    env = os.environ if env is None else env
    v = REP.version()
    d = {'hao': v.get('release') or FD.dev_label(v.get('commit')) or 'unknown',
         'where': FD.where(env)}
    d['macos'] = ('%s (%s)' % (_out(host, 'sw_vers', '-productVersion'), _out(host, 'sw_vers', '-buildVersion'))).strip()
    mem = _out(host, 'sysctl', '-n', 'hw.memsize')
    d['mac'] = ' · '.join(x for x in (_out(host, 'sysctl', '-n', 'hw.model'),
                                      _out(host, 'sysctl', '-n', 'machdep.cpu.brand_string'),
                                      '%d GB' % (int(mem) // (1 << 30)) if mem.isdigit() else '') if x)
    csr = host.csr_config()
    d['sip'] = 'unknown' if csr is None else ('on' if not csr else
                                              '%s (csr 0x%x)' % ('filesystem protection off' if csr & CSR_FS
                                                                 else 'partly off', csr))
    d['boot_args'] = _out(host, 'nvram', 'boot-args').partition('\t')[2] or '(none)'
    record = Path(state) / 'install-record.json'
    try:
        rec = json.loads(record.read_text())
        d['installed'] = '%s · %s · %s' % (rec.get('date') or rec.get('created') or '?', rec.get('release') or '?',
                                            ', '.join(rec.get('packs', [])) or 'packs not recorded')
    except (OSError, ValueError):
        d['installed'] = 'no install record' if d['where'] != 'installed' else \
            'no install record (this install did not write one)'
    try:
        import livepatch as LP
        recs = LP.records(state)
    except (ImportError, OSError, ValueError):
        recs = []
    d['live_patches'] = ('%d, newest %s (%s)' % (len(recs), recs[-1].get('id'), recs[-1].get('state'))
                         if recs else 'none')
    caches = [p for folder in aot_dirs if Path(folder).is_dir() for p in Path(folder).glob('*.aot')]
    if d['where'] == 'installed':
        import aot as AOT
        d['aot'] = AOT.summary(aot_dirs[0])
    else:
        d['aot'] = ('%d caches' % len(caches)) if caches else 'not on this Mac'
    d['update'] = 'not checked'
    stop = last_stop(REP.log_dirs(env) if folders is None else folders)
    d['last_stop'] = ('unknown: %s' % stop[2] if stop[0] == '?' else '%s on %s: %s' % stop) if stop else 'none'
    d['logs'] = str(REP.log_dir(env))
    return d


ROWS = (('hao', 'hao'), ('Runs on', 'where'), ('macOS', 'macos'), ('Mac', 'mac'), ('SIP', 'sip'),
        ('Boot args', 'boot_args'), ('Installed', 'installed'), ('Live patches', 'live_patches'),
        ('AOT caches', 'aot'), ('Update', 'update'), ('Last stop', 'last_stop'), ('Logs', 'logs'))


def show(c, d):
    """The status on console C: a panel on a Rich terminal, else labelled lines; --log json gets the event only."""
    c.event('status', **d)
    d = dict(d, where=FD.PLACES.get(d['where'], d['where']))
    import safety as SAFE
    d = SAFE.clean(d)                       # values come from files and other programs: shown as text
    plain = ['%-13s%s' % (label, d[key]) for label, key in ROWS]

    def panel():
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        grid = Table.grid(padding=(0, 3))
        grid.add_column(style=c.style('dim'), no_wrap=True)
        grid.add_column(overflow='fold')
        for label, key in ROWS:
            value = d[key]
            style = c.style('warn') if key == 'last_stop' and value != 'none' else None
            grid.add_row(label, Text(value, style=style or ''))
        return Panel(grid, title=Text.from_ansi(c.paint('bold', 'hao status')), title_align='left',
                     border_style=c.style('dim'), padding=(1, 2), expand=False)
    c.block(panel, plain, 'quiet')
    return 0


def status(a, c, host=None):
    return show(c, gather(host or MAC.SystemHost()))
