#!/usr/bin/env python3.14
"""lock.py: read inputs locks and target files (plain data) for the pipeline's shell stages.

  lock.py path LOCK NAME          the row's path; a row with a sha256 is checked against the file or folder
  lock.py take LOCK NAME DIR [FILE]
                                  copy row NAME's file (or FILE instead) into new folder DIR, hashing what is copied;
                                  the copy's path when it is the row's sha256, else nothing is left and it stops
  lock.py hash PATH               a row's sha256 for PATH: a file's sha256, or a folder's content_sha256
  lock.py seal LOCK NAME          give the '- PATH' row NAME in folder LOCK the sha256 of what PATH holds now
                                  (LOCK may be a target .json: NAME is then a key holding {"path", "sha256"})
  lock.py pin-local LOCK NAME     record what the row holds now as a local pin ($MACOS27_LOCAL_PINS; see local_pins)
  lock.py verify-store [--state FILE] [--full]
                                  every pinned row of pipeline/inputs whose path is in the store, rehashed when its
                                  fingerprint (size, mtime, inode; a folder's newest mtime and entry count) moved since
                                  the last good check (--full: all of them); names each changed row with its newest
                                  files' times and exits 1. State: store/.verify-store.json
  lock.py target TARGET KEY       a value from targets/<model>.json (dotted KEY; objects print as JSON)
  lock.py kdk-name DMG            Kernel_Debug_Kit_26.7.2_build_25G330.dmg -> KDK_26.7.2_25G330.kdk
  lock.py psp OCLP_SOURCE         the PatcherSupportPkg version an OCLP source tree expects
  lock.py key STORE LOCKS ITEM... a 12-hex cache key over ITEMs (pipeline/run's pristine and oclp stage keys);
                                  LOCKS = lock folders, ':'-separated, searched in order for a row
  lock.py key-lines STORE LOCKS ITEM...   the lines that key hashes (to see why a key changed)
  lock.py oclp-keys STORE LOCKS PATCHES DIR ITEM... < HOOKS
                                  the oclp stage's cache keys (pipeline/run): HOOKS = the set's oclp-hook plan lines,
                                  ITEM... = the stage's own items, DIR = a point's cache folder with {key} in it.
                                  Prints `full KEY`, `before NAME KEY DIR` per kc-hook NAME, and `resume NAME` for the
                                  last point whose DIR/target.img exists (oclp_points)

A key item is row:NAME, json:TARGET.json, file:PATH, patch:PATCH_DIR or text:STRING (see key_line). A key is made of
what the inputs are, never of where the store is or of a row's comments: a row counts by its sha256 column, so moving
the store or editing a comment keeps cache hits and any hashed content change misses.

A lock is a folder with one file per row (pipeline/inputs/<build>/<NAME>, pipeline/inputs/common/<NAME>), so rows
can be added independently. The file holds `SHA256-or-- PATH [# comment]`; lines starting with # are comments,
except `# check: KIND key=value ...` (see checks: a folder row checked by its own manifest). A
single file of `NAME SHA256-or-- PATH` rows is also read (tests, one-off locks). PATH may start with ~; a relative
PATH (`store/...`, `patches/...`) is relative to the repository root.
"""
import hashlib, json, os, re, stat, sys
from pathlib import Path

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(p):
    """A row path: ~ expanded, a relative path taken from the repository root."""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(TOP, p)


LOCAL_PINS_ENV = 'MACOS27_LOCAL_PINS'


def pin_key(lock, name):
    return os.path.relpath(os.path.abspath(lock), TOP) + '/' + name


def local_pins():
    """Locally built digests, {"<lock folder relative to the repository>/<row>": sha256}, from the JSON file named by
    $MACOS27_LOCAL_PINS (unset: none). For rows whose artifact this Mac builds itself with a toolchain other than the
    one the tracked pin was made with; the tracked rows stay as they are, and a pin here only replaces a sha256."""
    f = os.environ.get(LOCAL_PINS_ENV)
    if not f:
        return {}
    try:
        with open(f) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        sys.exit('%s: %s' % (f, e))
    if not isinstance(data, dict):
        sys.exit('%s: want a JSON object' % f)
    bad = [str(k) for k, v in data.items()
           if not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v)]
    if bad:
        sys.exit('%s: not a sha256: %s' % (f, ', '.join(sorted(bad))))
    return data


def pin_local(lock, name):
    """Record the sha256 of what row NAME of folder LOCK holds now as a local pin ($MACOS27_LOCAL_PINS)."""
    f = os.environ.get(LOCAL_PINS_ENV)
    if not f:
        sys.exit('set %s to the local pins file first' % LOCAL_PINS_ENV)
    lock = os.path.abspath(lock)
    r = rows(lock, tracked=True)
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    want, p = r[name]
    has_manifest = any(kind == 'payload' and 'manifest_sha256' in kv for kind, kv in checks(lock, name))
    if want == '-' and not has_manifest:
        sys.exit('%s: %s is unchecked (-)' % (lock, name))
    if not os.path.exists(p):
        sys.exit('%s: %s is not there' % (name, p))
    key = pin_key(lock, name)
    pins = dict(local_pins())
    h = want
    if want != '-':
        h = row_hash(lock, name, p)
        pins[key] = h
    if has_manifest:
        manifest = os.path.join(p, 'payload-manifest.json')
        if not os.path.isfile(manifest):
            sys.exit('%s: %s has no payload-manifest.json' % (name, p))
        pins[key + '#manifest_sha256'] = sha256(manifest)
    bad = check_row(lock, name, p, pins=pins)
    if bad:
        sys.exit('\n'.join(bad))
    os.makedirs(os.path.dirname(os.path.abspath(f)), exist_ok=True)
    tmp = f + '.tmp'
    with open(tmp, 'w') as t:
        json.dump(pins, t, indent=1, sort_keys=True)
    os.replace(tmp, f)
    return '%s %s (was %s)' % (h, name, want)


def rows(lock, tracked=False):
    out = {}
    if os.path.isdir(lock):
        for name in sorted(os.listdir(lock)):
            if name.startswith('.'):
                continue
            data = [l.split('#', 1)[0].split() for l in open(os.path.join(lock, name)) if not l.lstrip().startswith('#')]
            data = [d for d in data if d]
            if len(data) != 1 or len(data[0]) != 2:
                sys.exit('%s/%s: want one line SHA256|- PATH' % (lock, name))
            out[name] = (data[0][0], resolve(data[0][1]))
        return out if tracked else pinned(lock, out)
    for line in open(lock):
        line = line.split('#', 1)[0].split() if not line.lstrip().startswith('#') else []
        if line:
            if len(line) != 3:
                sys.exit('%s: bad row %r (want NAME SHA256|- PATH)' % (lock, ' '.join(line)))
            if line[0] in out:
                sys.exit('%s: row %s twice' % (lock, line[0]))
            out[line[0]] = (line[1], resolve(line[2]))
    return out if tracked else pinned(lock, out)


def pinned(lock, out):
    """OUT with each row's sha256 replaced by its local pin; a '-' (unchecked) row stays unchecked."""
    pins = local_pins()
    if not pins:
        return out
    return {n: (pins.get(pin_key(lock, n), v[0]) if v[0] != '-' else v[0], v[1]) for n, v in out.items()}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 22), b''):
            h.update(b)
    return h.hexdigest()


CHECK = re.compile(r'^#\s*check:\s*(\S+)((?:\s+[a-z_0-9]+=\S+)*)\s*$')


def checks(lock, name):
    """A folder row's `# check: KIND key=value ...` lines (a lock folder's row file only), as [(KIND, {key: value})].
    `# check: payload os_build=B layout_version=N [manifest_sha256=H]` makes the row a macos27tool-runtime payload checked by
    the m27-contract checker in tools/m27-contract (payload-manifest.json: clean, OS build, install layout, every file's sha256)."""
    f = os.path.join(lock, name)
    if not os.path.isdir(lock) or not os.path.isfile(f):
        return []
    out = []
    for line in open(f):
        if re.match(r'^#\s*check:', line):
            m = CHECK.match(line.rstrip('\n'))
            if not m:
                sys.exit('%s: bad check line %r (want # check: KIND key=value ...)' % (f, line.strip()))
            out.append((m.group(1), dict(kv.split('=', 1) for kv in m.group(2).split())))
    return out


def check_row(lock, name, p, pins=None):
    """Problems with row NAME at P by its check lines ([] when it has none or all pass)."""
    bad = []
    pins = local_pins() if pins is None else pins
    for kind, kv in checks(lock, name):
        if kind != 'payload':
            bad.append('%s: unknown check %s' % (name, kind))
            continue
        missing = {'os_build', 'layout_version'} - set(kv)
        if missing:
            bad.append('%s: check payload needs %s' % (name, ', '.join(sorted(missing))))
            continue
        manifest = os.path.join(p, 'payload-manifest.json')
        want_manifest = pins.get(pin_key(lock, name) + '#manifest_sha256', kv.get('manifest_sha256'))
        if want_manifest and os.path.isfile(manifest) and sha256(manifest) != want_manifest:
            bad.append('%s: %s is not %s' % (name, manifest, want_manifest))
            continue
        import runpy   # the vendored checker, as pipeline/tests/test_contract.py loads it
        contract = runpy.run_path(os.path.join(TOP, 'tools', 'm27-contract', 'm27_contract.py'))
        # a listed file under the row's `# hash: without` paths may be absent (made later on the user's Mac);
        # when it is there its sha256 is still checked
        absent_ok = without(lock, name)
        bad += ['%s: %s' % (name, b) for b in contract['check_payload'](
            TOP, {'manifest': manifest, 'os_build': kv['os_build'], 'layout_version': int(kv['layout_version'])})
            if not optional_missing(b, absent_ok)]
    return bad


def optional_missing(problem, paths):
    """True for check_payload's 'payload: REL is listed but missing' when REL is one of PATHS or below one."""
    m = re.match(r'^payload: (.+) is listed but missing$', problem)
    return bool(m) and any(m.group(1) == w or m.group(1).startswith(w.rstrip('/') + '/') for w in paths)


def in_scope(rel, only):
    """Whether relative path REL is covered by ONLY (relative paths; a folder covers everything below it), or is a
    folder above one of them."""
    return any(rel == o or rel.startswith(o.rstrip('/') + '/') or o.startswith(rel + '/') for o in only)


def content_sha256(top, only=None, without=()):
    """sha256 over a folder: one line per entry in sorted walk order (relative name, permission bits, and the file's
    sha256, the link's target text, or 'dir'). Ownership and times are left out: the builder stages as root.
    ONLY (a list of relative paths) limits it to those entries and the folders above them (scope())."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files + dirs):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, top)
            if only is not None and not in_scope(rel, only):
                continue
            if any(rel == w or rel.startswith(w.rstrip('/') + '/') for w in without):
                continue
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                body = 'link ' + os.readlink(p)
            elif stat.S_ISDIR(st.st_mode):
                body = 'dir'
            elif stat.S_ISREG(st.st_mode):
                body = 'file ' + sha256(p)
            else:
                sys.exit('%s: not a file, folder or symlink' % p)
            h.update(('%s %04o %s\n' % (os.path.relpath(p, top), stat.S_IMODE(st.st_mode), body)).encode())
    return h.hexdigest()


def row_sha256(p, only=None, without=()):
    """What a row's sha256 column says about PATH: the file's sha256 or the folder's content_sha256 (of ONLY, without
    WITHOUT)."""
    if os.path.isdir(p):
        return content_sha256(p, only, without)
    return sha256(p)


HASH_SCOPE = re.compile(r'^#\s*hash:\s*release-files(\s|$)')
HASH_WITHOUT = re.compile(r'^#\s*hash:\s*without\s+(\S.*?)\s*$')


def without(lock, name):
    """The entries a folder row's sha256 leaves out: a `# hash: without NAME...` line names them (relative paths,
    a folder leaves out everything below it): the producer's build record (dates, paths), notes, intermediates, so a
    remake of the same payload on another Mac matches the row."""
    f = os.path.join(lock, name)
    if not os.path.isdir(lock) or not os.path.isfile(f):
        return ()
    out = []
    for l in open(f):
        m = HASH_WITHOUT.match(l.rstrip('\n'))
        if m:
            out += [w for w in m.group(1).split('#', 1)[0].split() if w]
    return tuple(out)


def scope(lock, name):
    """The files a folder row's sha256 covers, or None (all of them). A row file with a `# hash: release-files` line
    covers what a release can give a user's Mac for it: its pipeline/release-classes.json `include` (the published
    files) plus its `made_on_mac_files` (the Apple-derived ones the user's Mac adds), so a store made from a release
    holds the same row hash as the store it was cut from."""
    f = os.path.join(lock, name)
    if not os.path.isdir(lock) or not os.path.isfile(f) or not any(HASH_SCOPE.match(l.rstrip('\n')) for l in open(f)):
        return None
    entry = json.load(open(os.path.join(TOP, 'pipeline', 'release-classes.json')))['rows'].get(name) or {}
    if not entry.get('include'):
        sys.exit('%s: "# hash: release-files", but release-classes.json gives %s no include list' % (f, name))
    return list(entry['include']) + [m['path'] for m in entry.get('made_on_mac_files', [])]


def row_hash(lock, name, p):
    """Row NAME's sha256 as its lock folder LOCK defines it, for the thing at P."""
    return row_sha256(p, scope(lock, name), without(lock, name))


def in_checkout(p):
    """A tracked path of this checkout (pinned by the commit), not something in the store."""
    rp = os.path.realpath(p)
    top = os.path.realpath(TOP)
    store = os.path.realpath(os.path.join(TOP, 'store'))
    return rp.startswith(top + os.sep) and not rp.startswith(store + os.sep)


def seal(lock, name):
    """Write the sha256 of what a '- PATH' row's PATH holds now into the row (comments kept)."""
    f = os.path.join(lock, name)
    want, p = rows(lock)[name]
    if want != '-':
        sys.exit('%s already has a sha256' % f)
    if not os.path.exists(p):
        sys.exit('%s: %s is not there' % (f, p))
    h = row_hash(lock, name, p)
    lines = open(f).read().splitlines()
    i = next(i for i, l in enumerate(lines) if l.split() and not l.lstrip().startswith('#'))
    lines[i] = h + lines[i].lstrip()[1:]
    open(f, 'w').write('\n'.join(lines) + '\n')
    return '%s %s' % (h, name)


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


def source_check(lock, name, p):
    """Stop when a name on the way to row NAME's source P (links followed one at a time: names_on_the_way) belongs to
    someone other than root, this user or the user who ran sudo. Its bytes are checked on the copy as well."""
    mine = {0, os.geteuid()} | ({int(os.environ['SUDO_UID'])} if os.environ.get('SUDO_UID', '').isdigit() else set())
    for q in names_on_the_way(p):
        st = os.lstat(q)
        # a volume mounted without ownership reports uid 99 for every entry; accepted there
        if st.st_uid not in mine and not (st.st_uid == UNKNOWN_UID and ignores_owners(q)):
            sys.exit('%s: %s belongs to another user (uid %d) (%s)' % (lock, q, st.st_uid, name))


def path(lock, name):
    r = rows(lock) if not lock.endswith('.json') else \
        {k: (v['sha256'], resolve(v['path'])) for k, v in json.load(open(lock)).items() if isinstance(v, dict) and 'path' in v}
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    want, p = r[name]
    if want != '-' and not os.path.exists(p):
        sys.exit('%s: %s is not there (%s)' % (lock, p, name))
    if want != '-' and (row_sha256(p) if lock.endswith('.json') else row_hash(lock, name, p)) != want:
        sys.exit('%s: %s is not %s (%s)' % (lock, p, want, name))
    bad = check_row(lock, name, p)
    if bad:
        sys.exit('\n'.join('%s: %s' % (lock, b) for b in bad))
    return p


def _no_acl(path, tree=False):
    """Remove every ACL entry of PATH (and of every entry under it, no link followed, when TREE)."""
    import subprocess
    # chmod -R -N follows a symbolic link (relative ones then resolve from the wrong folder and fail), so every entry
    # but the links is cleared; a link carries no data another user could write through.
    command = (['/usr/bin/find', path, '!', '-type', 'l', '-exec', '/bin/chmod', '-N', '{}', '+'] if tree
               else ['/bin/chmod', '-h', '-N', path])
    if subprocess.run(command, capture_output=True).returncode:
        sys.exit('%s: its access control entries could not be removed' % path)


def _has_acl(path):
    import subprocess
    out = subprocess.run(['/bin/ls', '-lde', path], capture_output=True, text=True).stdout
    return any(re.match(r'\s*\d+: ', l) for l in out.splitlines()[1:])


def private_dir(parent):
    """A new folder in PARENT for a copy until it is checked: made by this process (mode 0700, this user's, no ACL
    entry, checked), so no one else can open, change or replace what is in it."""
    import tempfile
    d = tempfile.mkdtemp(prefix='.stage-', dir=parent)
    _no_acl(d)
    st = os.lstat(d)
    # A volume mounted without ownership (a USB installer drive) reports every entry's owner as uid 99; accepted
    # there. Everywhere else the folder must be this user's.
    owner_ok = st.st_uid == os.geteuid() or (st.st_uid == UNKNOWN_UID and ignores_owners(d))
    if not stat.S_ISDIR(st.st_mode) or not owner_ok or stat.S_IMODE(st.st_mode) != 0o700 or _has_acl(d):
        sys.exit('%s: the staging folder is not private' % d)
    return d


UNKNOWN_UID = 99    # what a volume mounted without ownership reports as every entry's owner


def ignores_owners(path):
    """True when PATH is on a volume mounted with ownership ignored (mount(8) lists it as noowners)."""
    import subprocess
    df = subprocess.run(['/bin/df', '-P', path], capture_output=True, text=True)
    lines = df.stdout.strip().splitlines()
    if df.returncode or len(lines) < 2:
        return False
    mountpoint = lines[-1].split(None, 5)[-1]
    for line in subprocess.run(['/sbin/mount'], capture_output=True, text=True).stdout.splitlines():
        device, _, rest = line.partition(' on ')
        point, _, flags = rest.rpartition(' (')
        if point == mountpoint:
            return 'noowners' in flags
    return False


def own(path):
    """PATH and every entry under it (no link followed) given to this process's user and group, with no ACL entry: no
    other owner or access entry of the source survives into a copy. Times and permission bits are kept."""
    uid, gid = os.geteuid(), os.getegid()
    entries = [path]
    if os.path.isdir(path) and not os.path.islink(path):
        for d, dirs, files in os.walk(path):
            entries += [os.path.join(d, e) for e in dirs + files]
    for e in entries:
        st = os.lstat(e)
        if (st.st_uid, st.st_gid) != (uid, gid):
            os.lchown(e, uid, gid)
    _no_acl(path, tree=os.path.isdir(path) and not os.path.islink(path))


def _expose(made, dest):
    """Move the checked copy MADE to DEST, which must not be there (never replaced)."""
    if os.path.lexists(dest):
        sys.exit('%s is there already' % dest)
    os.rename(made, dest)


def take(lock, name, dest, src=None):
    """A private copy of row NAME's file (or SRC) in DEST, hashed as it is written: read once, so what is checked is
    what later steps read. Stops unless the row has a sha256 and the copy has it."""
    r = rows(lock)
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    want, p = r[name]
    src = src or p
    if want == '-':
        sys.exit('%s: row %s has no sha256' % (lock, name))
    if not os.path.isfile(src):
        sys.exit('%s: %s is not a file (%s)' % (lock, src, name))
    source_check(lock, name, src)
    out = os.path.join(dest, os.path.basename(src))
    if os.path.lexists(out):
        sys.exit('%s is there already (%s)' % (out, name))
    import shutil
    stage_dir = private_dir(dest)          # only this call's own folder is removed on failure
    try:
        tmp = os.path.join(stage_dir, os.path.basename(src))
        h = hashlib.sha256()
        with open(src, 'rb') as f, open(tmp, 'xb') as o:
            for b in iter(lambda: f.read(1 << 22), b''):
                h.update(b); o.write(b)
        own(tmp)
        if h.hexdigest() != want:
            sys.exit('%s: %s is not %s (%s)' % (lock, src, want, name))
        _expose(tmp, out)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    return out


def _clone(src, dst):
    """Clone file SRC to DST (APFS clonefile, never following a link, owned by the caller), else copy it."""
    try:
        import ctypes
        clonefile = ctypes.CDLL(None, use_errno=True).clonefile
    except (OSError, AttributeError):
        clonefile = None
    if clonefile is None or clonefile(os.fsencode(src), os.fsencode(dst), 3):   # CLONE_NOFOLLOW | CLONE_NOOWNERCOPY
        import shutil
        shutil.copy2(src, dst, follow_symlinks=False)


def copy_tree(src, dest, only=None, without=()):
    """Copy folder SRC into the existing folder DEST: the entries a row's sha256 covers (ONLY, WITHOUT: as
    content_sha256), with their permission bits and links as links, files cloned where they can be. Nothing outside
    the row's hash is copied, so a checked copy holds only checked bytes."""
    modes = []
    for d, dirs, files in os.walk(src):
        rel_d = os.path.relpath(d, src)
        keep = []
        for f in sorted(dirs + files):
            p = os.path.join(d, f)
            rel = os.path.normpath(os.path.join(rel_d, f))
            if only is not None and not in_scope(rel, only):
                continue
            if any(rel == w or rel.startswith(w.rstrip('/') + '/') for w in without):
                continue
            q = os.path.join(dest, rel)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                os.symlink(os.readlink(p), q)
            elif stat.S_ISDIR(st.st_mode):
                os.mkdir(q, 0o700)
                modes.append((q, stat.S_IMODE(st.st_mode)))
                keep.append(f)
            elif stat.S_ISREG(st.st_mode):
                _clone(p, q)
            else:
                sys.exit('%s: not a file, folder or symlink' % p)
        dirs[:] = [x for x in dirs if x in keep]
    for q, mode in reversed(modes):
        os.chmod(q, mode)


def take_tree(lock, name, dest):
    """A copy of folder row NAME in DEST (made here; it must not exist), checked as a tree after it is made (its
    content sha256 and check lines): read the copy, never the row's path, which may change after a check. Stops
    unless the row has a sha256 and the copy has it; on a failure only DEST, which this call made, is removed."""
    r = rows(lock)
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    want, p = r[name]
    if want == '-':
        sys.exit('%s: row %s has no sha256' % (lock, name))
    if not os.path.isdir(p):
        sys.exit('%s: %s is not a folder (%s)' % (lock, p, name))
    source_check(lock, name, p)
    if os.path.lexists(dest):
        sys.exit('%s is there already (%s)' % (dest, name))
    import shutil
    stage_dir = private_dir(os.path.dirname(os.path.abspath(dest)))
    try:
        tmp = os.path.join(stage_dir, 'tree')
        os.mkdir(tmp, 0o700)
        copy_tree(p, tmp, scope(lock, name), without(lock, name))
        own(tmp)                           # before the check: nothing of the source's owners or ACLs is kept
        if row_hash(lock, name, tmp) != want:
            sys.exit('%s: %s is not %s (%s)' % (lock, p, want, name))
        bad = check_row(lock, name, tmp)
        if bad:
            sys.exit('\n'.join('%s: %s' % (lock, b) for b in bad))
        os.chmod(tmp, 0o755)
        _expose(tmp, dest)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    return dest


def where(lock, name):
    """Row NAME's path, unchecked: only to decide where a checked copy goes (stage), never to read it."""
    r = rows(lock)
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    return r[name][1]


def stage(lock, name, dest, tool='cp'):
    """Copy row NAME to DEST (it must not exist) as before a check (cp -Rpc: clones, times and permission bits; TOOL
    ditto: ditto) into a private folder (private_dir), give the copy to this user without ACL entries (own), check it
    (its sha256 or content sha256 and check lines) and only then move it to DEST: what is checked is what later steps
    read, never the row's path, which may change after a check. A '-' row is copied only when it is part of this
    checkout (pinned by the commit). On a failure nothing is left."""
    import shutil, subprocess
    r = rows(lock)
    if name not in r:
        sys.exit('%s has no row %s' % (lock, name))
    want, p = r[name]
    if want == '-' and not in_checkout(p):
        sys.exit('%s: row %s has no sha256' % (lock, name))
    if not os.path.exists(p):
        sys.exit('%s: %s is not there (%s)' % (lock, p, name))
    if os.path.lexists(dest):
        sys.exit('%s: %s is there already (%s)' % (lock, dest, name))
    source_check(lock, name, p)
    # copied into a private folder of this call, given to this user without ACL entries, then checked; only then
    # moved to DEST: no one else can open or change the copy between the check and its use
    stage_dir = private_dir(os.path.dirname(os.path.abspath(dest)))
    try:
        tmp = os.path.join(stage_dir, os.path.basename(dest))
        cmd = ['/usr/bin/ditto', p, tmp] if tool == 'ditto' else ['/bin/cp', '-Rpc' if os.path.isdir(p) else '-pc', p, tmp]
        if subprocess.run(cmd).returncode:
            sys.exit('%s: copying %s failed (%s)' % (lock, p, name))
        st = os.lstat(tmp)
        if stat.S_ISLNK(st.st_mode) or stat.S_ISDIR(st.st_mode) != os.path.isdir(p):
            sys.exit('%s: the copy of %s is not a %s (%s)' % (lock, p, 'folder' if os.path.isdir(p) else 'file', name))
        own(tmp)
        # a file row's sha256 covers its bytes only: no set-id bit, and no one else may write it; a folder's
        # permission bits below it are in its content sha256, its own are not
        os.chmod(tmp, stat.S_IMODE(st.st_mode) & 0o755)
        if want != '-' and row_hash(lock, name, tmp) != want:
            sys.exit('%s: %s is not %s (%s)' % (lock, p, want, name))
        bad = check_row(lock, name, tmp)
        if bad:
            sys.exit('\n'.join('%s: %s' % (lock, b) for b in bad))
        _expose(tmp, dest)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    return dest


def row_identity(sha, p, store):
    """What a row's input is, independent of the store's location: its sha256 column; for a '-' row the file's own
    content digest, else (a folder, or not there yet) its path relative to the store (outside the store: the path).
    A '-' folder (a whole unpacked macOS tree) is named by its path rather than hashed, which would be slow."""
    if sha != '-':
        return sha
    if os.path.isfile(p):
        return sha256(p)
    rp, rs = os.path.realpath(p), os.path.realpath(store)
    return 'store/' + os.path.relpath(rp, rs) if rp.startswith(rs + os.sep) else rp


def canonical_target(v):
    """A target file's data without row paths and notes: an object with a sha256 counts by that hash."""
    if isinstance(v, dict):
        if 'path' in v and isinstance(v.get('sha256'), str) and v['sha256'] != '-':
            v = {k: x for k, x in v.items() if k not in ('path', 'note')}
        return {k: canonical_target(x) for k, x in v.items()}
    if isinstance(v, list):
        return [canonical_target(x) for x in v]
    return v


def tree_digest(top):
    """sha256 over a folder's relative names and file contents (symlinks by their target text)."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
            p = os.path.join(d, f)
            body = 'link ' + os.readlink(p) if os.path.islink(p) else sha256(p)
            h.update(('%s %s\n' % (os.path.relpath(p, top), body)).encode())
    return h.hexdigest()


def key_line(item, store, locks):
    kind, _, arg = item.partition(':')
    if kind == 'row':
        for lock in locks:
            if os.path.isfile(os.path.join(lock, arg)):
                sha, p = rows(lock)[arg]
                return 'row %s %s' % (arg, row_identity(sha, p, store))
        sys.exit('key: no row %s in %s' % (arg, ' '.join(locks)))
    if kind == 'json':
        data = canonical_target(json.load(open(arg)))
        return 'json %s %s' % (os.path.basename(arg), hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())
    if kind == 'file':
        return 'file %s %s' % (os.path.basename(arg), sha256(arg))
    if kind == 'patch':
        rev = json.load(open(os.path.join(arg, 'patch.json'))).get('revision')
        return 'patch %s r%s %s' % (os.path.basename(arg.rstrip('/')), rev, tree_digest(os.path.join(arg, 'src')))
    if kind == 'text':
        return 'text ' + ' '.join(arg.split())
    sys.exit('key: bad item %r (want row:, json:, file:, patch: or text:)' % item)


def key_lines(store, locks, *items):
    return '\n'.join(key_line(i, store, [l for l in locks.split(':') if l]) for i in items)


def key(store, locks, *items):
    return hashlib.sha256((key_lines(store, locks, *items) + '\n').encode()).hexdigest()[:12]


def patch_directory(name, root):
    return os.path.join(root, name)


def oclp_points(hooks, patches):
    """The oclp stage's cache points for the set's oclp-hook plan lines (`oclp-hook NAME ROW...`, set order), as
    [(NAME, applied kc-hooks, plan lines)]: (None, all kc-hooks, every line) for the whole stage (after the snapshot),
    then one point before each kc-hook NAME. oclp.sh runs OCLP (with every hook.sh) in one builder session and each
    kc-hook in a session of its own, so the state before kc-hook NAME is the target after OCLP and the kc-hooks
    before NAME: its lines leave out only the kc-hook-only patches from NAME on (a patch with a hook.sh counts in every
    point, OCLP ran it). Two sets that reach the same state share the point (no NAME in it)."""
    def has(line, f):
        return os.path.isfile(os.path.join(patch_directory(line.split()[1], patches), 'src', f))
    kc = [l for l in hooks if has(l, 'kc-hook.sh')]
    points = [(None, [l.split()[1] for l in kc], list(hooks))]
    for i, line in enumerate(kc):
        points.append((line.split()[1], [l.split()[1] for l in kc[:i]],
                       [l for l in hooks if l not in kc[i:] or has(l, 'hook.sh')]))
    return points


def oclp_keys(store, locks, patches, template, *base, hooks=None):
    if hooks is None:
        hooks = sys.stdin.read()
    hooks = [' '.join(l.split()) for l in hooks.splitlines() if l.strip()]
    out, resume = [], None
    for name, applied, lines in oclp_points(hooks, patches):
        items = list(base)
        if name:
            items.append('text:point after kc-hooks ' + ' '.join(applied))
        items.append('text:' + '\n'.join(lines))
        for l in lines:
            l = l.split()
            items += ['patch:' + patch_directory(l[1], patches)] + ['row:' + r for r in l[2:]]
        k = key(store, locks, *items)
        if not name:
            out.append('full ' + k)
            continue
        d = template.replace('{key}', k)
        out.append('before %s %s %s' % (name, k, d))
        if os.path.isfile(os.path.join(d, 'target.img')):
            resume = name
    if resume:
        out.append('resume ' + resume)
    return '\n'.join(out)


def target(file, key):
    v = json.load(open(file))
    for k in key.split('.'):
        v = v[k]
    if isinstance(v, str):
        return os.path.expanduser(v) if v.startswith('~/') else v
    return json.dumps(v, indent=1)


def kdk_name(dmg):
    m = re.fullmatch(r'Kernel_Debug_Kit_([\d.]+)_build_(\w+)\.dmg', os.path.basename(dmg))
    if not m:
        sys.exit('not a KDK dmg name: ' + dmg)
    return 'KDK_%s_%s.kdk' % m.groups()


def psp(src):
    text = open(os.path.join(src, 'opencore_legacy_patcher/constants.py')).read()
    return re.search(r'patcher_support_pkg_version:\s*str\s*=\s*"([^"]+)"', text).group(1)


def fingerprint(p):
    """What changes when anything writes P (a file, or every entry under a folder): sizes, mtimes, inodes, count."""
    st = os.lstat(p)
    if not os.path.isdir(p):
        return [st.st_size, st.st_mtime_ns, st.st_ino]
    n, newest = 0, st.st_mtime_ns
    for d, dirs, files in os.walk(p):
        for e in dirs + files:
            n += 1
            newest = max(newest, os.lstat(os.path.join(d, e)).st_mtime_ns)
    return [n, newest]


def newest_files(p, k=5):
    """The K newest entries under P (or P itself): (iso time, relative path), newest first."""
    import datetime
    entries = [(os.lstat(p).st_mtime, '.')]
    if os.path.isdir(p):
        for d, dirs, files in os.walk(p):
            entries += [(os.lstat(os.path.join(d, e)).st_mtime, os.path.relpath(os.path.join(d, e), p)) for e in dirs + files]
    return [(datetime.datetime.fromtimestamp(m).astimezone().isoformat(timespec='seconds'), r)
            for m, r in sorted(entries, reverse=True)[:k]]


def store_locks(inputs=os.path.join(TOP, 'pipeline', 'inputs')):
    """Every lock folder under INPUTS (BUILD, common, golden/BUILD)."""
    out = []
    for d, dirs, files in os.walk(inputs):
        dirs.sort()
        if any(not f.startswith('.') for f in files):
            out.append(d)
    return out


def verify_store(*argv):
    """Rehash the pinned store rows whose fingerprint moved; report changed ones. Returns the report text, exits 1
    when a row changed."""
    argv = list(argv)
    state_path = argv[argv.index('--state') + 1] if '--state' in argv else os.path.join(TOP, 'store', '.verify-store.json')
    full = '--full' in argv
    try:
        state = json.load(open(state_path))
    except (OSError, ValueError):
        state = {}
    store = os.path.realpath(os.path.join(TOP, 'store'))
    bad, checked, skipped, seen = [], 0, 0, set()
    for lock in store_locks():
        for name, (want, p) in rows(lock).items():
            rp = os.path.realpath(p)
            if want == '-' or not rp.startswith(store + os.sep) or not os.path.exists(p) or (rp, want) in seen:
                continue
            seen.add((rp, want))
            key = '%s %s' % (want, os.path.relpath(rp, store))
            fp = fingerprint(p)
            if not full and state.get(key) == fp:
                skipped += 1
                continue
            checked += 1
            got = row_hash(lock, name, p)
            if got == want:
                state[key] = fp
            else:
                state.pop(key, None)
                bad.append('%s/%s: %s is not %s; newest: %s' % (os.path.relpath(lock, TOP), name, os.path.relpath(p, TOP),
                           want, ', '.join('%s %s' % e for e in newest_files(p))))
    tmp = state_path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, state_path)
    report = 'verify-store: %d rehashed, %d unchanged, %d changed' % (checked, skipped, len(bad))
    if bad:
        sys.exit('\n'.join(bad + [report]))
    return report


if __name__ == '__main__':
    cmd, args = sys.argv[1], sys.argv[2:]
    print({'path': path, 'take': take, 'take-tree': take_tree, 'stage': stage, 'where': where, 'hash': row_sha256, 'seal': seal, 'target': target, 'kdk-name': kdk_name, 'psp': psp, 'key': key, 'key-lines': key_lines,
           'oclp-keys': oclp_keys, 'verify-store': verify_store, 'pin-local': pin_local}[cmd](*args))
