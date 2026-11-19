"""macOS27Tool aot: the runtime's ahead-of-time caches for the programs the release ships (front end for /usr/libexec/hao --aot).

  sudo macOS27Tool aot               build the caches of the default set; ones still valid are skipped
  macOS27Tool aot --status           valid, missing and stale caches (the launcher's --check) and the hao.aot job's state
  sudo macOS27Tool aot --background  the same build, detached, at background priority; its log in /private/var/log/hao
  sudo macOS27Tool aot --clear       remove /private/var/db/hao/*.aot and the job's marker (the job builds again at start-up)

The caches live in /private/var/db/hao, which root, daemons and every user read; so building and clearing need root.
The hao.aot LaunchDaemon (hao --aot --auto) builds them once per runtime after start-up; `hao status` shows its state.
"""
import os
from pathlib import Path
import re
import subprocess

from console import Stopped

LAUNCHER = '/usr/libexec/hao'
CACHES = Path('/private/var/db/hao')
MARKER = 'auto-aot.done'
LOG = Path('/private/var/log/hao')
STEP = re.compile(r'^hao-aot: \[(\d+)/(\d+)\] (\S.*?)(?:: (valid, skipped|valid|missing|stale|not checkable))?$')
BUILT = re.compile(r'^hao-aot: (\d+) programs cached, (\d+) already valid, (\d+) failed, (\d+) listed but not present')
CHECKED = re.compile(r'^hao-aot: (\d+) valid, (\d+) missing, (\d+) stale, (\d+) not checkable, (\d+) listed but not present')


def launcher(env):
    path = env.get('HAO_LAUNCHER') or LAUNCHER
    if not os.access(path, os.X_OK):
        raise Stopped('there is no hao runtime at %s' % path, code=5, untouched='nothing was built or removed',
                      why='macOS27Tool aot builds the caches of the runtime that macOS 27 with hao installs')
    return path


def need_root(euid, what):
    if euid != 0:
        raise Stopped('macOS27Tool aot %s needs root' % what, code=5, untouched='nothing was built or removed',
                      why='the caches are in /private/var/db/hao, which root, daemons and every user read',
                      options=['run it as: sudo macOS27Tool aot%s' % ('' if what == 'build' else ' --' + what)])


def job(caches=CACHES, run=subprocess.run):
    """The hao.aot job: (state, detail): 'running', 'done' (for which runtime) or 'not yet'."""
    try:
        r = run(['/bin/launchctl', 'print', 'system/hao.aot'], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and re.search(r'^\s*state = running', r.stdout, re.M):
            return 'running', 'building now, at background priority'
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return 'done', (Path(caches) / MARKER).read_text().strip()
    except OSError:
        return 'not yet', 'runs after the first start-up, once a user is logged in'


def summary(caches=CACHES, run=subprocess.run):
    """One line for hao status: the caches here and the job."""
    n = len(list(Path(caches).glob('*.aot'))) if Path(caches).is_dir() else 0
    state, detail = job(caches, run)
    return '%d caches · auto job %s%s' % (n, state, ' (%s)' % detail if state != 'not yet' else '')


class AotView:
    """The launcher's [N/M] lines as one bar; its summary line as the result."""

    def __init__(self, runner, checking):
        self.r, self.c, self.checking = runner, runner.c, checking
        self.task = None
        self.counts = {}
        self.resumed = False

    def start(self):
        self.task = self.c.task('  ' + ('check the caches' if self.checking else 'build the caches'), total=1, unit='programs')

    def line(self, text):
        m = STEP.match(text)
        if m:
            n, total, path, what = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
            if what:
                self.counts[what.split(',')[0]] = self.counts.get(what.split(',')[0], 0) + 1
            self.task.update(done=n, total=total, tail=Path(path).name)
            return
        for pattern, keys in ((BUILT, ('cached', 'valid', 'failed', 'absent')),
                              (CHECKED, ('valid', 'missing', 'stale', 'unchecked', 'absent'))):
            m = pattern.match(text)
            if m:
                self.r.summary.update(zip(keys, map(int, m.groups())))

    def command(self, cmd):
        pass

    def step(self, name):
        pass

    def progress(self, done, total, unit):
        self.task.update(done=done, total=total)

    def tick(self):
        self.task.update()

    def end(self, rc):
        s = self.r.summary
        if self.checking:
            note = '%d valid, %d missing, %d stale' % (s.get('valid', 0), s.get('missing', 0), s.get('stale', 0))
            self.task.final_note = note
            self.task.end(not (s.get('missing') or s.get('stale') or s.get('unchecked')))
        else:
            self.task.final_note = '%d built, %d still valid, %d failed' % (s.get('cached', 0), s.get('valid', 0),
                                                                           s.get('failed', 0))
            self.task.end(rc == 0)


def aot(a, c, env=None, euid=None, run=subprocess.run, caches=CACHES, popen=subprocess.Popen):
    import progress as PROG
    env = os.environ if env is None else env
    euid = os.geteuid() if euid is None else euid
    exe = launcher(env)
    if a.clear:
        need_root(euid, 'clear')
        removed = 0
        for p in Path(caches).glob('*.aot'):
            p.unlink()
            removed += 1
        if (Path(caches) / MARKER).is_file():
            (Path(caches) / MARKER).unlink()
        c.done('removed %d caches from %s; the hao.aot job builds them again after the next start-up' % (
            removed, caches), removed=removed)
        return 0
    if a.background:
        need_root(euid, 'background')
        import safety as SAFE
        log = LOG / 'aot.log'
        with SAFE.own_folder(LOG, 0o755) as folder, folder.open_log('aot.log') as out:
            argv = ['/usr/sbin/taskpolicy', '-b', exe, '--aot'] if os.access('/usr/sbin/taskpolicy', os.X_OK) else [exe, '--aot']
            popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True)
        c.done('building the caches in the background; macOS27Tool aot --status shows how far, the log is %s' % log)
        return 0
    if not a.status:
        need_root(euid, 'build')
    runner = PROG.Runner(c, 'aot', popen=popen)
    runner.view_for = lambda argv: AotView(runner, a.status)
    rc = runner([exe, '--aot'] + (['--check'] if a.status else []))
    s = runner.summary
    if a.status:
        state, detail = job(caches, run)
        c.print('  %-12s%s' % ('auto job', state + (' · ' + detail if detail else '')))
        c.done('%d valid, %d missing, %d stale%s' % (s.get('valid', 0), s.get('missing', 0), s.get('stale', 0),
                                                    '' if not (s.get('missing') or s.get('stale')) else
                                                    ': sudo macOS27Tool aot builds them'), **s)
        return 0
    if rc:
        raise Stopped('%d of the caches could not be built' % s.get('failed', 0) if s.get('failed') else
                      'the runtime stopped (exit %d)' % rc, untouched='the caches built before it stopped are kept',
                      options=['the log: %s' % runner.log, 'run it again: sudo macOS27Tool aot (built caches are skipped)'])
    c.done('%d caches built, %d still valid' % (s.get('cached', 0), s.get('valid', 0)), **s)
    return 0
