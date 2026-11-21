"""Safe file and text handling for m27 when it runs as root or handles other people's data.

  harden_env(env)        run as root: drop m27's developer variables (they choose what root runs, reads or writes),
                         use the system PATH and, under sudo, the sudo user's home from the account database; called
                         before m27 computes any path. M27_STICK stays only for the m27 that runs from that drive;
                         M27_MIRROR and M27_MIRROR_STRICT (KEPT) always stay.
  Folder(path, owners)   a folder opened once without following a link at its name, checked through its descriptor
                         (a folder, its owner, not writable by others, and where the kernel says it is: no link above
                         it), then listed, read, created in and appended to only through that descriptor, so a folder
                         swapped after the check changes nothing
  create(path, data)     a new file, never through a symlink or over an existing file; mode set on the descriptor
  others_can_change(p)   the first folder at or above P another user could change, or None (checked before a root run
                         reads or writes there)
  as_user(owner, fn)     fn() in a child with the sudo user's ids and groups, under a deadline
  text(s), clean(v)      external text for the terminal: control characters and bidi overrides made visible
"""
import os
import re
import stat
import sys
from pathlib import Path

# m27's own variables a user's environment could carry into a root run; each one changes what root executes, reads,
# writes or where it looks (developer overrides; M27_STICK is set by the USB installer's launcher, where only root exists)
DEVELOPER = ('M27_LAUNCHER', 'M27_LOGS', 'M27_RECORD', 'M27_ANYWHERE', 'M27_STICK', 'M27_DISK_FLOOR_GB',
             'M27_NO_RICH', 'M27_PALETTE')
# kept through a root run (never in DEVELOPER): they only choose where the pinned downloads come from, and every byte
# from the mirror is checked against the release's size and sha256 (tools/acquire, pipeline/unpack/ranged.py), so a
# root create-installer does not quietly go to the origin instead
KEPT = ('M27_MIRROR', 'M27_MIRROR_STRICT')
SYSTEM_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
UNKNOWN_UID = 99            # a volume mounted with owners ignored (the USB installer's M27 in the Recovery)
HERE = Path(__file__).resolve()
AS_USER_SECONDS = 120


def elevated(env=None, euid=None):
    """Root started by a user through sudo."""
    env = os.environ if env is None else env
    return (os.geteuid() if euid is None else euid) == 0 and bool(env.get('SUDO_USER') or env.get('SUDO_UID'))


def runs_from(stick, here=HERE):
    """Whether this m27 (HERE) is the one on the drive STICK, a mounted volume /Volumes/NAME: only then is M27_STICK
    the launcher's (not "/", not any folder that happens to contain m27)."""
    try:
        s = Path(stick).resolve() if stick else None
        return (s is not None and s.parent == Path('/Volumes') and os.path.ismount(str(s))
                and Path(here).resolve().is_relative_to(s))
    except (OSError, ValueError):
        return False


# the interpreter tools/macOS27Tool/macOS27Tool's launcher runs as root, after checking it and its whole tree are root's alone
ROOT_PYTHON = '/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14'


def harden_env(env=None, euid=None, home_of=None, here=HERE, executable=None):
    """As root: remove DEVELOPER variables and TMPDIR, set PATH to the system's. M27_STICK stays only when no user
    started this root (no sudo) and this m27 runs from that drive (the USB installer's launcher). Under sudo HOME
    becomes the sudo user's home as the account database has it. Returns the names removed."""
    env = os.environ if env is None else env
    if (os.geteuid() if euid is None else euid) != 0:
        return []
    by_sudo = elevated(env, 0)
    keep_stick = not by_sudo and runs_from(env.get('M27_STICK'), here)
    gone = [k for k in DEVELOPER + ('TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR')     # root picks its own CA bundle
            if k in env and k not in KEPT and not (k == 'M27_STICK' and keep_stick)]
    for k in gone:
        del env[k]
    env['PATH'] = SYSTEM_PATH
    # the pipeline's host scripts start through `env python3.14`: as root they get exactly the interpreter the launcher
    # verified (only when this run is that interpreter), never one from the user's PATH
    exe = executable if executable is not None else sys.executable
    if exe == ROOT_PYTHON:
        env['PATH'] = os.path.dirname(ROOT_PYTHON) + ':' + SYSTEM_PATH
    if by_sudo:
        home = (home_of or _home_of)(env)
        if home:
            env['HOME'] = home
        else:
            env.pop('HOME', None)
    return gone


SYSTEM_CA = '/etc/ssl/cert.pem'


def ca_bundle(env=None, paths=None, system=SYSTEM_CA):
    """The CA bundle for this run's HTTPS: python.org's Python has none until its "Install Certificates" runs (every
    download would fail with CERTIFICATE_VERIFY_FAILED), so when the Python carries no bundle and none is named,
    SSL_CERT_FILE is set to macOS's own (SYSTEM, the system's, root-owned) for m27 and everything it starts. Returns the
    bundle set, or None when nothing was changed."""
    env = os.environ if env is None else env
    if env.get('SSL_CERT_FILE') or env.get('SSL_CERT_DIR'):
        return None                                   # named by the user (as root harden_env dropped them)
    if paths is None:
        import ssl
        paths = ssl.get_default_verify_paths()
    if paths.cafile or paths.capath or not os.path.isfile(system):
        return None
    env['SSL_CERT_FILE'] = system
    return system


def _home_of(env):
    import pwd
    try:
        if env.get('SUDO_UID', '').isdigit():
            return pwd.getpwuid(int(env['SUDO_UID'])).pw_dir
        return pwd.getpwnam(env.get('SUDO_USER', '')).pw_dir
    except (KeyError, ValueError):
        return None


def stick_owners(path, env=None):
    """{UNKNOWN_UID} when PATH is on the USB installer this m27 runs from (owners ignored there), else empty."""
    env = os.environ if env is None else env
    stick = env.get('M27_STICK')
    try:
        if stick and runs_from(stick) and Path(path).resolve().is_relative_to(Path(stick).resolve()):
            return {UNKNOWN_UID}
    except (OSError, ValueError):
        pass
    return set()


def sudo_owner(env=None):
    """(uid, gid) of the user who ran sudo, when m27 runs as root through sudo; else None."""
    env = os.environ if env is None else env
    if os.geteuid() == 0 and env.get('SUDO_UID', '').isdigit() and env.get('SUDO_GID', '').isdigit():
        return int(env['SUDO_UID']), int(env['SUDO_GID'])
    return None


def as_user(owner, fn, seconds=AS_USER_SECONDS):
    """FN() run in a child process with OWNER's (uid, gid) and that user's groups; its return value (a string) comes
    back. Root writing for a user does it as that user, so the user's folders (and any link in them) give root
    nothing: the kernel checks the user's own rights. A child that does not finish in SECONDS is killed."""
    import pwd
    import select
    import signal
    import time
    try:
        groups = os.getgrouplist(pwd.getpwuid(owner[0]).pw_name, owner[1])
    except KeyError:
        groups = [owner[1]]
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            os.close(r)
            os.setgroups(groups)
            os.setgid(owner[1])
            os.setuid(owner[0])
            out = fn()
            os.write(w, ('ok\0' + str(out)).encode())
            code = 0
        except BaseException as x:          # noqa: B902 - reported to the parent, never raised in the child
            try:
                os.write(w, ('err\0%s\0%s' % (type(x).__name__, x)).encode())
            except OSError:
                pass
        finally:
            os._exit(code)
    os.close(w)
    data, deadline = b'', time.monotonic() + seconds
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([r], [], [], left)[0]:
                os.kill(pid, signal.SIGKILL)
                raise OSError('the writer did not finish in %d s' % seconds)
            chunk = os.read(r, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(r)
        os.waitpid(pid, 0)
    parts = data.decode(errors='replace').split('\0')
    if parts[0] == 'ok':
        return parts[1]
    kind, msg = (parts[1], parts[2]) if len(parts) >= 3 else ('OSError', 'the writer stopped')
    raise (FileExistsError if kind == 'FileExistsError' else OSError)(msg)


def create(path, data, mode=0o600, dir_fd=None):
    """Write DATA (str or bytes) to a new file at PATH (a name in DIR_FD when given): O_EXCL and O_NOFOLLOW, so
    neither an existing file nor a symlink planted at the name is written through; mode set on the descriptor."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0), mode,
                 dir_fd=dir_fd)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb', closefd=False) as f:
            f.write(data.encode() if isinstance(data, str) else data)
    finally:
        os.close(fd)
    return Path(path)


WRITE_RIGHTS = ('write', 'append', 'add_file', 'add_subdirectory', 'delete_child', 'delete', 'writeattr',
                'writeextattr', 'writesecurity', 'chown')


def acl_allows_write(path):
    """Whether PATH's access list (macOS extended ACL, not followed through a link) has an allow entry with a right that
    changes it or its contents. A deny-only list (a home folder's "everyone deny delete") is not one."""
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
    libc.acl_get_link_np.restype = ctypes.c_void_p
    libc.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.acl_to_text.restype = ctypes.c_void_p
    libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    libc.acl_free.argtypes = [ctypes.c_void_p]
    acl = libc.acl_get_link_np(os.fsencode(str(path)), 0x00000100)     # ACL_TYPE_EXTENDED
    if not acl:
        return ctypes.get_errno() not in (2, 45)            # ENOENT: no list; EOPNOTSUPP: the volume has none
    try:
        text = libc.acl_to_text(acl, None)
        if not text:
            return True                                                  # unreadable: assume the worst
        try:
            lines = ctypes.string_at(text).decode('utf-8', 'replace').splitlines()
        finally:
            libc.acl_free(text)
    finally:
        libc.acl_free(acl)
    for line in lines[1:]:                                               # line 0: "!#acl 1"
        fields = line.split(':')
        if len(fields) >= 5 and fields[-2] == 'allow' and set(fields[-1].split(',')) & set(WRITE_RIGHTS):
            return True
    return False


def others_can_change(path, env=None, euid=None, owners=True):
    """The first existing folder at or above PATH (links resolved) that a user other than root, this one or the one who
    ran sudo could change: writable by group or others without the sticky bit, or (OWNERS) owned by someone else.
    None when there is none. On the USB installer (OWNERS False) only root runs, so only the mode bits count."""
    env = os.environ if env is None else env
    euid = os.geteuid() if euid is None else euid
    allowed = {0, euid, UNKNOWN_UID}
    if euid == 0 and env.get('SUDO_UID', '').isdigit():
        allowed.add(int(env['SUDO_UID']))
    raw = str(path) if os.path.isabs(path) else os.path.join(os.getcwd(), str(path))   # '..' kept for the kernel's reading
    p = Path(os.path.realpath(raw)) if not os.path.lexists(raw) else raw
    while not os.path.lexists(p) and Path(p) != Path(p).parent:
        p = Path(p).parent
    # every name on the way, links followed hop by hop, and the folders holding them
    names = names_on_the_way(p)
    chain = list(dict.fromkeys([os.path.realpath(p)] + [os.path.dirname(n) for n in names] +
                               [str(x) for x in Path(os.path.realpath(p)).parents]))
    for n in names:
        st = os.lstat(n)
        if owners and st.st_uid not in allowed:
            return '%s belongs to another user (uid %d)' % (n, st.st_uid)
    for folder in chain:
        try:
            st = os.stat(folder)
        except OSError:
            continue
        sticky = stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX
        if st.st_mode & 0o022 and not sticky:
            return '%s can be written by others' % folder
        if owners and st.st_uid not in allowed:
            return '%s belongs to another user (uid %d)' % (folder, st.st_uid)
        if acl_allows_write(folder):
            return '%s has an access list that lets others change it' % folder
    return None


def names_on_the_way(p, limit=40):
    """Every name the kernel visits resolving P, links followed hop by hop (each link and each name its target passes
    through), in order; OSError after LIMIT links (a loop)."""
    # as the kernel reads it: '..' is taken after the link before it is followed, never folded away first
    queue = (str(p) if os.path.isabs(p) else os.path.join(os.getcwd(), str(p))).split('/')[1:]
    cur, out, hops = '/', [], 0
    while queue:
        c = queue.pop(0)
        if c in ('', '.'):
            continue
        if c == '..':
            cur = os.path.dirname(cur)
            continue
        name = os.path.join(cur, c)
        out.append(name)
        if os.path.islink(name):
            hops += 1
            if hops > limit:
                raise OSError('%s: too many links' % p)
            target = os.readlink(name)
            if target.startswith('/'):
                cur = '/'
            queue = target.split('/')[1 if target.startswith('/') else 0:] + queue
            continue
        cur = name
    return out


def others_can_change_tree(path, env=None, euid=None, owners=True, _seen=None):
    """others_can_change(PATH), then every folder and file below it (links are not followed and are themselves
    checked): the first one another user could change, or None. For a tree root reads or runs from (a kit)."""
    why = others_can_change(path, env, euid, owners)
    if why or not os.path.isdir(path):
        return why
    env = os.environ if env is None else env
    euid = os.geteuid() if euid is None else euid
    allowed = {0, euid, UNKNOWN_UID} | ({int(env['SUDO_UID'])} if euid == 0 and env.get('SUDO_UID', '').isdigit() else set())
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(top, name)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                if owners and st.st_uid not in allowed:
                    return '%s belongs to another user (uid %d)' % (p, st.st_uid)
                why = others_can_change(p, env, euid, owners)     # every hop of this link, even to a target seen before
                if why:
                    return '%s (through %s)' % (why, p)
                target = os.path.realpath(p)
                seen = set() if _seen is None else _seen
                if target in seen:
                    continue
                seen.add(target)
                why = others_can_change_tree(target, env, euid, owners, seen)   # what it reaches, and all in it
                if why:
                    return '%s (through %s)' % (why, p)
                continue
            if st.st_mode & 0o022:
                return '%s can be written by others' % p
            if owners and st.st_uid not in allowed:
                return '%s belongs to another user (uid %d)' % (p, st.st_uid)
            if acl_allows_write(p):
                return '%s has an access list that lets others change it' % p
    return None


def expected_path(p):
    """P as the kernel names it: absolute, with macOS's own /var, /tmp and /etc links resolved to /private/..."""
    path = os.path.abspath(str(p))
    for top in ('/var', '/tmp', '/etc'):
        if path == top or path.startswith(top + '/'):
            return '/private' + path
    return path


def fd_path(fd):
    """Where the kernel says the open file FD is (macOS F_GETPATH). Without it nothing can be pinned: refused."""
    import fcntl
    if not hasattr(fcntl, 'F_GETPATH'):
        raise OSError('this system cannot tell where an open folder is (no F_GETPATH)')
    return fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b'\0', 1)[0].decode('utf-8', errors='surrogateescape')


class Folder:
    """A folder pinned by its descriptor (see the module's note). OWNERS: whose folder (and files) it may be;
    PRIVATE: not writable by group or others (a folder of ours is narrowed to MODE through the descriptor first)."""

    def __init__(self, path, owners, create=False, mode=0o700, private=True):
        self.path = Path(path)
        if create:
            self.path.mkdir(parents=True, exist_ok=True, mode=mode)
        self.owners = set(owners)
        self.fd = os.open(str(self.path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0))
        try:
            st = os.fstat(self.fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid not in self.owners:
                raise OSError('%s is not a folder of %s' % (self.path, ', '.join(map(str, sorted(self.owners)))))
            if fd_path(self.fd).lower() != expected_path(self.path).lower():     # where it is, before any change
                raise OSError('%s is reached through a link' % self.path)
            if create and private and st.st_uid == os.geteuid() and st.st_mode & 0o7777 != mode:
                os.fchmod(self.fd, mode)                     # only a folder we write to, never one we only read
                st = os.fstat(self.fd)
            if private and st.st_uid != UNKNOWN_UID and st.st_mode & 0o022:
                raise OSError('%s can be written by others' % self.path)
        except BaseException:
            os.close(self.fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def entries(self, pattern=None, limit=100000):
        """[(name, stat)] of the plain files (not links) of the folder's owners; up to LIMIT entries are looked at
        (m27 keeps 30 logs: a folder with more was filled by something else, and is refused rather than half read)."""
        out = []
        with os.scandir(self.fd) as it:
            for n, e in enumerate(it):
                if n >= limit:
                    raise OSError('%s has more than %d entries' % (self.path, limit))
                if pattern and not re.fullmatch(pattern, e.name):
                    continue
                try:
                    st = os.stat(e.name, dir_fd=self.fd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and st.st_uid in self.owners:
                    out.append((e.name, st))
        return out

    def read(self, name, limit=4 << 20):
        """NAME's text: a plain file of the folder's owners, at most LIMIT bytes, through the checked descriptor."""
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0),
                         dir_fd=self.fd)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > limit or st.st_uid not in self.owners:
                return None
            with os.fdopen(fd, 'rb', closefd=False) as f:
                return f.read(limit + 1)[:limit].decode('utf-8', errors='replace')
        finally:
            os.close(fd)

    def create(self, name, data, mode=0o600):
        create(name, data, mode, dir_fd=self.fd)
        return self.path / name

    def unlink(self, name):
        os.unlink(name, dir_fd=self.fd)

    def open_log(self, name):
        """NAME to append to: never through a link, never a FIFO or device, never a file with another name (hard
        link) or another owner; created or narrowed to 0600."""
        import fcntl
        fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, 'O_CLOEXEC', 0), 0o600, dir_fd=self.fd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid not in self.owners:
                raise OSError('%s is not a plain log file of this user' % (self.path / name))
            os.fchmod(fd, 0o600)
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, 'a', encoding='utf-8', errors='replace')


def own_folder(path, mode=0o700, env=None):
    """A Folder of this process's user (or, on the USB installer this m27 runs from, its unknown owner), created
    MODE if missing."""
    return Folder(path, {os.geteuid()} | stick_owners(path, env), create=True, mode=mode)


def open_log(path, env=None):
    """PATH appended to, in its own folder of this user (Folder.open_log)."""
    path = Path(path)
    with own_folder(path.parent, env=env) as f:
        return f.open_log(path.name)


CONTROL = re.compile('[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]')


def clean(value):
    """VALUE (decoded JSON) with every string in it passed through text()."""
    if isinstance(value, str):
        return text(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {text(k) if isinstance(k, str) else k: clean(v) for k, v in value.items()}
    return value


def text(s):
    """S with control characters (escape sequences included) and bidi overrides shown as \\xNN / \\uNNNN, so text
    from backends, files or the network cannot move the cursor, recolour or rewrite what the terminal shows."""
    if not isinstance(s, str):
        s = str(s)
    return CONTROL.sub(lambda m: ('\\x%02x' % ord(m.group())) if ord(m.group()) < 0x100 else '\\u%04x' % ord(m.group()), s)
