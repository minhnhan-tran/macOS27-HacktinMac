#!/usr/bin/env python3.14
"""diskguard: refuse a job that would leave the host disk under its floor, before the job writes anything.

  diskguard.py check --need GB [--who NAME] [--what TEXT] [--path P]     exit 0 ok, 3 refused

A build, VM or capture that writes more than 10 GB calls check(NEED) first with what it really expects to write. When
M27_DISK_CHECK names a command (a host-wide guard with its own levels), that command decides:
`$M27_DISK_CHECK check --need GB --who NAME --what TEXT`, exit 3 = refused. Otherwise the job is refused when the free
space of the volume holding PATH, less NEED, is under M27_DISK_FLOOR_GB (default 150).
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys

FLOOR_GB = 150
REFUSED = 3


class Refused(Exception):
    pass


def free_gb(path='/'):
    """Free GB on the volume that holds PATH; a PATH not made yet (a fresh checkout's store) counts on its parent's."""
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return shutil.disk_usage(p).free / 1e9


def check(need, who='', what='', path=None, out=print):
    """None when the job may start; raises Refused (with the reason) when it would leave the disk under its floor."""
    command = os.environ.get('M27_DISK_CHECK', '').strip()
    if command:
        r = subprocess.run(shlex.split(command) + ['check', '--need', '%d' % -(-need // 1), '--who', who or '?',
                                                   '--what', what or '?'], capture_output=True, text=True)
        said = (r.stdout + r.stderr).strip()
        if r.returncode == REFUSED:
            raise Refused(said or 'the host disk guard refused %d GB' % need)
        if r.returncode:
            raise Refused('the host disk guard failed (exit %d): %s' % (r.returncode, said))
        if said:
            out(said)
        return
    floor = float(os.environ.get('M27_DISK_FLOOR_GB', FLOOR_GB))
    free = free_gb(path or os.path.expanduser('~'))
    if free - need < floor:
        raise Refused('%s %s needs %.0f GB; %.0f GB free would leave %.0f GB, under the floor of %.0f GB: free space '
                      'first' % (who or '?', what or '', need, free, free - need, floor))
    out('disk: %s %s: %.0f GB free, needs %.0f GB, leaves %.0f GB' % (who or '?', what or '', free, need, free - need))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    q = sub.add_parser('check')
    q.add_argument('--need', type=float, required=True)
    q.add_argument('--who', default='')
    q.add_argument('--what', default='')
    q.add_argument('--path')
    a = p.parse_args(argv)
    try:
        check(a.need, a.who, a.what, a.path)
    except Refused as x:
        print('diskguard: refused: %s' % x, file=sys.stderr)
        return REFUSED
    return 0


if __name__ == '__main__':
    sys.exit(main())
