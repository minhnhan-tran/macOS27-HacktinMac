"""hao run PROGRAM [ARGS]: an ARM program through the hao runtime, with its diagnostic switches.

The runtime's launcher (/usr/libexec/hao; HAO_LAUNCHER names another, for development) is internal: ARM-only programs
already run when started directly, so hao run is only for -v/-vv, --interp, --stats and --allow-unsigned. hao run
replaces itself with the launcher, so the program's output, exit status and signals are its own. Under sudo the
program runs as root with the engine and printing switches only: -v and -vv are dropped (run as yourself for those).
"""
import os

from console import Stopped

LAUNCHER = '/usr/libexec/hao'


def argv_for(a, euid, launcher):
    """(the launcher's argv, a note for the user or None)."""
    switches, note = [], None
    if a.verbose and euid == 0:
        note = '-v is ignored under sudo (the program runs as root); run hao run as yourself for diagnostics'
    elif a.verbose:
        switches.append('-' + 'v' * min(a.verbose, 2))
    switches += ['--interp'] if a.interp else []
    switches += ['--stats'] if a.stats else []
    switches += ['--allow-unsigned'] if a.allow_unsigned else []
    return [launcher] + switches + [a.program] + list(a.args), note


def run(a, c, env=None, euid=None, execv=os.execv):
    env = os.environ if env is None else env
    launcher = env.get('HAO_LAUNCHER') or LAUNCHER
    if not os.access(launcher, os.X_OK):
        raise Stopped('there is no hao runtime at %s' % launcher, code=5,
                      why='hao run starts programs through the runtime that macOS 27 with hao installs',
                      untouched='nothing was run')
    argv, note = argv_for(a, os.geteuid() if euid is None else euid, launcher)
    if note:
        c.warning(note)
    c.event('run', argv=argv)
    c.close()
    execv(launcher, argv)
    return 0
