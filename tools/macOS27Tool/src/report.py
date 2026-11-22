"""macOS27Tool report: one text file a developer needs to diagnose a failed run, made to attach to a GitHub issue.

It holds: m27's release and commit, this Mac (sw_vers, model, CPU, memory, SIP, boot arguments), the `macOS27Tool detect`
profile, the log of the last m27 command (create-installer, build-disk and install keep their console events in
LOGS: /Library/Logs/m27 when run as root, else ~/Library/Logs/m27), the install record
(/private/var/db/m27/install-record.json) and the live patch records, when there are any.

No private data: the serial number and hardware UUID, the computer and host names, user names (home folders become
USER), Wi-Fi network names, e-mail and hardware (MAC) addresses are replaced before anything is written.
"""
import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

import detect as DETECT
import safety as SAFE
import livepatch as LP

TOP = Path(__file__).resolve().parents[3]
SYSTEM_LOGS = Path('/Library/Logs/m27')
STATE = Path('/private/var/db/m27')
KEEP = 30                                     # command logs kept per folder
USER = 'USER'
HOME = re.compile(r'(/Users/)(?!Shared\b)([^/\s"\'\\]+)')
LABELLED = [
    # key or label, then its value: serials, UUIDs, Wi-Fi names and the like as plists, JSON and tool output show them
    (re.compile(r'((?:IOPlatformSerialNumber|serial[ _-]?number|SerialNumber|IOPlatformUUID|Hardware UUID|'
                r'Provisioning UDID)["\']?\s*(?:\([^)]*\))?\s*[:=]\s*["\']?)([^"\'\s,<]+)', re.I), '[removed]'),
    (re.compile(r'((?:SSID(?:_STR)?|Network ?Name|Current Network Information)["\']?\s*[:=]\s*["\']?)([^"\'\n,<]+)',
                re.I), '[wifi]'),
]
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
URL_SECRET = re.compile(r'(\b[a-z][a-z0-9+.-]*://)[^/\s@:]+(?::[^/\s@]*)?@', re.I)
URL_QUERY = re.compile(r'(\b[a-z][a-z0-9+.-]*://[^\s?#"\']+)\?[^\s#"\']+', re.I)
SECRET_NAMES = r'(?:password|passwd|passphrase|secret|token|api[_-]?key|access[_-]?key|auth(?:orization)?|cookie|session)'
SECRET_FIELD = re.compile(r'("[A-Za-z0-9_-]*%s[A-Za-z0-9_-]*"\s*:\s*)"(?:[^"\\]|\\.)*"' % SECRET_NAMES, re.I)
BEARER = re.compile(r'(\b(?:Bearer|Basic|token)\s+)[^\s"\',;&]+', re.I)
SECRET_WORD = re.compile(r'(\b[A-Za-z0-9_-]*%s[A-Za-z0-9_-]*\s*[=:]\s*)(?!\[removed\])(?:(?:Bearer|Basic)\s+)?[^\s"\',;&]+'
                         % SECRET_NAMES, re.I)
URL_FRAGMENT = re.compile(r'(\b[a-z][a-z0-9+.-]*://[^\s#"\']+)#[^\s"\']+', re.I)
SECRET_KEY = re.compile(SECRET_NAMES, re.I)
SECRET_FLAG = re.compile(r'^--?[A-Za-z0-9_-]*%s[A-Za-z0-9_-]*$' % SECRET_NAMES, re.I)
SECRET_ASSIGN = re.compile(r'^(--?[A-Za-z0-9_-]*%s[A-Za-z0-9_-]*=)' % SECRET_NAMES, re.I)


def redact(value):
    """VALUE (decoded JSON) with what secrets look like removed before it is written or shown: any value under a key
    named like one (password, token, api_key, ...), whatever its type, and the argument after a flag named like one
    (--password X); `--password=X` and free text are left to scrub()'s patterns."""
    if isinstance(value, dict):
        return {k: '[removed]' if isinstance(k, str) and SECRET_KEY.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        out, hide = [], False
        for v in value:
            out.append('[removed]' if hide else redact(v))
            hide = isinstance(v, str) and bool(SECRET_FLAG.match(v))
        return out
    if isinstance(value, str):
        m = SECRET_ASSIGN.match(value)                       # --password=two words: all of it
        return m.group(1) + '[removed]' if m else value
    return value


MAC_ADDRESS = re.compile(r'\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b')


def log_dir(env=None, euid=None):
    """Where this run keeps its command log: M27_LOGS, else the USB installer's logs/ (M27_STICK), else
    /Library/Logs/m27 as root, else ~/Library/Logs/m27. (Under sudo m27 drops M27_LOGS and M27_STICK first.)"""
    env = os.environ if env is None else env
    if env.get('M27_LOGS'):
        return Path(env['M27_LOGS'])
    if env.get('M27_STICK'):
        return Path(env['M27_STICK']) / 'logs'
    if (os.geteuid() if euid is None else euid) == 0:
        return SYSTEM_LOGS
    return Path(env.get('HOME') or Path.home()) / 'Library' / 'Logs' / 'm27'


def log_dirs(env=None):
    """Every folder a command log may be in, newest first is decided by the files themselves."""
    env = os.environ if env is None else env
    if env.get('M27_LOGS'):
        return [Path(env['M27_LOGS'])]
    out = [Path(env['M27_STICK']) / 'logs'] if env.get('M27_STICK') else []
    out += [SYSTEM_LOGS, Path(env.get('HOME') or Path.home()) / 'Library' / 'Logs' / 'm27']
    if env.get('SUDO_USER'):
        home = SAFE._home_of(env)
        if home:
            out.append(Path(home) / 'Library' / 'Logs' / 'm27')
    return list(dict.fromkeys(out))


VERSION = '0.2.0'


def version():
    """{'release', 'commit'}: release.json beside m27 names the release and the commit it was cut from; a checkout
    names its own commit."""
    out = {}
    rel = TOP / 'tools' / 'macOS27Tool' / 'release.json'
    if rel.is_file():
        import signing as SIGN
        try:
            copy, key, _ = SIGN.verified_release(rel)            # the bytes that were verified, read once
            r, out['release_signature'] = json.loads(copy.read_bytes()), 'verified (%s)' % key
        except SIGN.SignatureError as x:
            out['release_signature'] = 'NOT verified: %s' % x
            r = {'release': 'release.json beside m27 is not verified: its claims are not shown'}
        except (OSError, ValueError):
            r = {}
        if isinstance(r, dict) and r:
            out['release'], out['release_commit'] = r.get('release'), r.get('m27_patcher')
        else:
            out['release'] = 'release.json unreadable'
    stamp = TOP / 'tools' / 'macOS27Tool' / 'VERSION'          # written by the macos27tool-command recipe when it stages m27
    if not (TOP / '.git').exists() and stamp.is_file():
        out['commit'] = stamp.read_text().strip()
    if (TOP / '.git').exists():
        r = subprocess.run(['/usr/bin/git', '-c', 'safe.directory=%s' % TOP, '-C', str(TOP), 'describe', '--always',
                            '--dirty', '--abbrev=12'],
                           capture_output=True, text=True)
        if r.returncode == 0:
            out['commit'] = r.stdout.strip()
    return out


def save_log(command, argv, events, folder=None, ver=None, now=None):
    """Keep COMMAND's console events as FOLDER/COMMAND-DATE.json, 0600 in a 0700 folder, created new (never through a
    symlink or over a file); the oldest beyond KEEP go (plain files only). Returns the path, or None when it cannot be
    written."""
    folder = Path(folder) if folder else log_dir()
    stamp = (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
    data = json.dumps(redact({'command': command, 'argv': list(argv), 'm27': ver if ver is not None else version(),
                              'events': events}), indent=1, default=str) + '\n'
    try:
        with SAFE.own_folder(folder) as f:                  # pinned: a folder swapped after the check changes nothing
            path = None
            for n in range(10):
                try:
                    path = f.create('%s-%s%s.json' % (command, stamp, '-%d' % n if n else ''), data, 0o600)
                    break
                except FileExistsError:
                    continue
            if path is None:
                return None
            for name, _ in sorted(f.entries(LOG_NAME), key=lambda e: e[1].st_mtime)[:-KEEP]:
                f.unlink(name)
            return path
    except OSError:
        return None


def owners_of(folder, env=None):
    """(owners, private) of FOLDER for root to read command logs from it: the system log folder and the install
    record's folder are root's and must not be writable by others; the USB installer's logs are root's (or its
    volume's unknown owner, when this m27 runs from it); any other folder is the user's (the sudo user's under sudo)
    and only that user's files in it are read, never root's: a user's folder that leads elsewhere gives root nothing."""
    env = os.environ if env is None else env
    folder = Path(folder)
    if folder == SYSTEM_LOGS or folder == STATE:
        return {0}, True
    if env.get('M27_STICK') and folder == Path(env['M27_STICK']) / 'logs':
        return {0} | SAFE.stick_owners(folder, env), True
    if os.geteuid() == 0 and env.get('SUDO_UID', '').isdigit():
        return {int(env['SUDO_UID'])}, False
    return {os.geteuid()}, False


def log_folder(folder, env=None):
    """FOLDER pinned for reading (SAFE.Folder), or None when it is missing or not as owners_of says."""
    owners, private = owners_of(folder, env)
    try:
        return SAFE.Folder(folder, owners, private=private)
    except OSError:
        return None


LOG_NAME = r'[A-Za-z0-9._-]+-[0-9-]+\.json'


def candidates(folders, env=None):
    """[(mtime, path)] of command logs that root may read, newest first: plain files of the folder's owner in a
    pinned folder (log_folder); up to 10,000 entries per folder are looked at, all of them sorted."""
    found = []
    for f in folders:
        pinned = log_folder(f, env)
        if pinned is None:
            continue
        with pinned:
            try:
                found += [(st.st_mtime, Path(f) / name) for name, st in pinned.entries(LOG_NAME)]
            except OSError:
                continue
    return sorted(found, reverse=True)


def read_log(path, env=None):
    """PATH's text, read through its pinned folder (log_folder), or None."""
    pinned = log_folder(Path(path).parent, env)
    if pinned is None:
        return None
    with pinned:
        return pinned.read(Path(path).name)


def last_log(folders, env=None):
    """The newest command log root may read (candidates())."""
    found = candidates(folders, env)
    return found[0][1] if found else None


def _out(host, *argv):
    r = host.run(list(argv), check=False)
    return r.stdout.decode(errors='replace').strip() if r.returncode == 0 else ''


def secrets(host, env):
    """{text: replacement} of what identifies this Mac or its owner."""
    found = {}
    pe = {}
    try:
        p = plistlib.loads(host.run(['ioreg', '-a', '-r', '-d', '1', '-c', 'IOPlatformExpertDevice'],
                                    check=False).stdout or b'')
        pe = p[0] if isinstance(p, list) and p else p if isinstance(p, dict) else {}
    except Exception:
        pass
    for key, label in (('IOPlatformSerialNumber', '[serial]'), ('IOPlatformUUID', '[uuid]')):
        if isinstance(pe.get(key), str):
            found[pe[key]] = label
    for argv in (['scutil', '--get', 'ComputerName'], ['scutil', '--get', 'LocalHostName'], ['hostname']):
        name = _out(host, *argv)
        if name:
            found[name] = '[computer name]'
            found[name.split('.')[0]] = '[computer name]'
    for key in ('SUDO_USER', 'USER', 'LOGNAME'):
        if env.get(key) and env[key] != 'root':
            found[env[key]] = USER
            full = _out(host, 'id', '-F', env[key])
            if full:
                found[full] = '[name]'
    home = env.get('HOME', '')
    if home.startswith('/Users/') and home.count('/') == 2:
        found[home.rsplit('/', 1)[1]] = USER
    return {k: v for k, v in found.items() if len(k) >= 3}


def scrub(text, private):
    """TEXT with PRIVATE's strings ({text: replacement}) and every labelled serial, UUID, Wi-Fi name, e-mail and
    hardware address replaced; the name of each home folder becomes USER."""
    text = HOME.sub(lambda m: m.group(1) + USER, text)
    for pattern, label in LABELLED:
        text = pattern.sub(lambda m: m.group(1) + label, text)
    text = URL_SECRET.sub(lambda m: m.group(1) + '[credentials]@', text)    # https://user:pass@host
    text = SECRET_FIELD.sub(lambda m: m.group(1) + '"[removed]"', text)       # "password": "..."
    text = BEARER.sub(lambda m: m.group(1) + '[removed]', text)               # Bearer ...
    text = SECRET_WORD.sub(lambda m: m.group(1) + '[removed]', text)          # token=..., password: ...
    text = URL_FRAGMENT.sub(lambda m: m.group(1) + '#[fragment]', text)
    text = URL_QUERY.sub(lambda m: m.group(1) + '?[query]', text)             # tokens in query strings
    text = EMAIL.sub('[email]', text)
    text = MAC_ADDRESS.sub('[hardware address]', text)
    for secret in sorted(private, key=len, reverse=True):
        text = re.sub(r'(?<![A-Za-z0-9])%s(?![A-Za-z0-9])' % re.escape(secret), private[secret], text)
    return text


def _json(path, env=None):
    text = read_log(path, env)
    if text is None:
        return 'unreadable: %s is not a plain file in a plain folder of its owner, or is larger than 4 MB' % path
    try:
        return json.dumps(redact(json.loads(text)), indent=1, default=str, ensure_ascii=False)
    except RecursionError:
        return 'unreadable: nested too deeply'
    except (OSError, ValueError) as x:
        return 'unreadable: %s' % x


def collect(host, env=None, folders=None, state=STATE, ver=None, now=None):
    """The report's text, scrubbed."""
    env = dict(os.environ) if env is None else env
    folders = log_dirs(env) if folders is None else folders
    when = now or datetime.datetime.now()
    parts = ['macOS27Tool report  %s' % when.strftime('%Y-%m-%d %H:%M:%S'),
             'For a GitHub issue. Removed before writing: serial number, hardware UUID, computer name, user names, '
             'Wi-Fi names, e-mail and hardware addresses, credentials and query strings in URLs. Read it before you '
             'share it: messages from programs can hold other details.']

    def section(title, body):
        parts.extend(['', '== ' + title, body.rstrip() or '(none)'])

    v = ver if ver is not None else version()
    section('m27', '\n'.join('%-15s %s' % (k, v[k]) for k in ('release', 'release_signature', 'release_commit', 'commit')
                            if v.get(k))
            or 'version unknown (no release.json and no git checkout)')
    csr = host.csr_config()
    boot_args = _out(host, 'nvram', 'boot-args').partition('\t')[2]
    mem = _out(host, 'sysctl', '-n', 'hw.memsize')
    section('This Mac', '\n'.join([
        _out(host, 'sw_vers'),
        'model          %s' % _out(host, 'sysctl', '-n', 'hw.model'),
        'cpu            %s' % _out(host, 'sysctl', '-n', 'machdep.cpu.brand_string'),
        'memory         %s' % ('%d GB' % (int(mem) // (1 << 30)) if mem.isdigit() else 'unknown'),
        'machine        %s' % _out(host, 'uname', '-m'),
        'kernel         %s' % _out(host, 'uname', '-v'),
        'SIP (csr)      %s' % ('unknown' if csr is None else '0x%x' % csr),
        'boot-args      %s' % (boot_args or '(none)'),
        'running as     %s' % ('root' if host.euid() == 0 else 'a user')]))
    try:
        p = DETECT.profile(DETECT.facts(host))
        p['target'] = DETECT.target_for_model(p['model'])[0]
        section('macOS27Tool detect', json.dumps(p, indent=1))
    except (DETECT.DetectError, LP.PatchError, OSError, KeyError, ValueError) as x:
        section('macOS27Tool detect', 'failed: %s' % x)
    log = last_log(folders, env)
    section('Last command' + (' (%s)' % log.name if log else ''), _json(log) if log else
            'no m27 command log (looked in %s)' % ', '.join(str(f) for f in folders))
    others = [p.name for _, p in candidates(folders, env) if p != log]
    if others:
        section('Earlier command logs', '\n'.join(others[:KEEP]))
    record = Path(state) / 'install-record.json'
    section('Install record', _json(record) if record.is_file() else 'none (%s)' % record)
    try:
        recs = LP.records(state)
    except (OSError, ValueError):
        recs = []
    if recs:
        section('Live patches', '\n'.join('%s %s packs %s snapshot %s' % (r.get('id'), r.get('state'),
                                                                          ','.join(r.get('packs', [])), r.get('snapshot'))
                                          for r in recs))
    return scrub('\n'.join(parts) + '\n', secrets(host, env))


def default_output(now=None, cwd=None):
    """m27-report-DATE.txt in the current folder (the release folder the user ran m27 from), else /private/tmp."""
    name = 'm27-report-%s.txt' % (now or datetime.datetime.now()).strftime('%Y%m%d-%H%M%S')
    cwd = Path(cwd or os.getcwd())
    return (cwd if os.access(cwd, os.W_OK) else Path('/private/tmp')) / name


def write(path, text, env=None, numbered=False):
    """Write the report; under sudo, give it to the user who ran sudo so they can attach and delete it."""
    env = os.environ if env is None else env
    path = Path(path)
    owner = SAFE.sudo_owner(env)

    def new():
        # a new file only: an existing name (or a symlink planted at it) is never written through; a name the user
        # gave is refused when it exists, a default one (NUMBERED) gets -1 ... -9
        for n in range(10 if numbered else 1):
            candidate = path if n == 0 else path.with_name('%s-%d%s' % (path.stem, n, path.suffix))
            try:
                return str(SAFE.create(candidate, text, 0o600))
            except FileExistsError:
                continue
        raise FileExistsError('%s exists already' % path if not numbered else
                              '%s and nine numbered names next to it exist already' % path)
    # under sudo the file is written by the sudo user itself, so the user's folders (and any link in them) can lead
    # root nowhere: the kernel checks the user's own rights
    return Path(SAFE.as_user(owner, new) if owner else new())


def report(a, c, host, env=None, folders=None, state=STATE, ver=None):
    path = Path(a.output).expanduser() if getattr(a, 'output', None) else default_output()
    text = collect(host, env, folders, state, ver)
    given = bool(getattr(a, 'output', None))
    if given:
        try:
            path = write(path, text, env)
        except FileExistsError:
            from console import Stopped
            raise Stopped('%s exists' % path, why='macOS27Tool report writes a new file and never replaces one',
                          options=['name another file with --output, or leave --output out'],
                          untouched='nothing was written')
    else:
        try:
            path = write(path, text, env, numbered=True)
        except OSError:                  # not writable here, or every default name taken: a new name in /private/tmp
            path = write(Path('/private/tmp') / path.name, text, env, numbered=True)
    c.event('report', path=str(path), bytes=len(text))
    c.done('report written to %s' % path.resolve(), path=str(path))
    c.print('Read it before you attach it to a GitHub issue: m27 removed serials, user and Wi-Fi names, addresses '
            'and the credentials it recognises; anything else a program printed is still there.', 'quiet')
    return 0
