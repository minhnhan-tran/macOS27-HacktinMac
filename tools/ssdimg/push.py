"""Stream images to / from a target Mac's disks through ssdpush-agent, from a Recovery it loads over the network.

    ssdimg push TARGET [--rev R | --image FILE [--chunk-size N]] [--delta | --full] [--no-verify] [--usb IDENTITY]
                [--yes]
    ssdimg verify TARGET [--rev R | --image FILE [--chunk-size N]] [--usb IDENTITY]
    ssdimg pull TARGET [--force]

TARGET is `rec@ADDR:/dev/diskN`: a macOS Recovery on the target Mac answering root SSH at ADDR, and the target's
internal disk in it. `local:/path/to/file` runs the agent locally
against a pre-sized file (tests).

push sends each data chunk exactly as stored (zstd) with its BLAKE2b hash; the agent decompresses, checks the hash,
writes, and reads back to verify. Unallocated space is skipped; allocated all-zero chunks are sent as zero frames
(TRIM). --full sends all allocated chunks without the initial comparison. Afterwards OpenCore's launcher is set
Disabled, so the firmware's default (NetBoot) stays first, and the APFS container is grown to the disk.

S25 ("write only what changed; verify everything; fall back to a full write on any mismatch"). There is no host-side
record of what a disk holds (the drive's own writes and macOS journals change it), only on-target hashes:
  - delta (default): if the target's primary GPT (MBR, header, entries) is not the one this push leaves, or more than
    half of the present chunks differ from the image, "target does not match the image: full write" and every present
    chunk is written (the GPT check comes first and skips the hash query); otherwise only the differing chunks;
  - every present chunk and the relocated GPT are then re-read and hash-checked; a failure after a delta write falls
    back once to a full write and a second verify; a failure after any full write is an error (exit 1, first bad
    offsets named);
  - one line per push: "push summary: mode=... queried=... matched=... written=... bytes=... fallback=0|1 seconds:
    gpt-check= query= write= verify= [fallback-write= fallback-verify=]".
--image FILE pushes a raw disk image without a repository: present chunks (MBR and GPTs, non-APFS partitions whole,
allocated APFS blocks) are hashed from the file, chunk size --chunk-size (default 256 KiB), same logic.
--usb IDENTITY writes an external drive of the laptop instead (see guard_usb, usb_guard): IDENTITY =
'media=MEDIA NAME,size=BYTES,lun=N|-' must match exactly; partition tables are zeroed first and the MBR and primary
GPT written last; zero chunks are written, not TRIMmed; the launcher and the container size are left alone.
Tests: SSDPUSH_TEST_FAULT (agent.c) flips a byte of a file target after a write phase; SSDPUSH_LOCAL_AGENT names the
agent `local:` targets run.

--from-disk SOURCE (laptop to laptop): the image is another laptop's internal disk, both laptops in their NetBoot
Recovery (SOURCE = rec@ADDR:/dev/diskN; local:path in tests). The source disk is opened read-only by its own agent
(--read-only): its GPT and APFS allocation are read as `pull` reads them, and its agent hashes every present chunk
('H'). The target is then reached THROUGH the source's agent (--relay): the host sends 'S' frames (a chunk index and
its hash) and the source reads, checks and compresses that chunk and sends it straight to the target agent, so the
data never passes through the host. Delta, verify and the full-write fallback are as above (S25); the source's
volumes are unmounted first so it cannot change meanwhile, and a chunk that changed since it was hashed stops the push.
pull is `capture` over the link: the allocation map is read remotely, then only the allocated chunks are copied into
the working image (commit afterwards as usual).
"""
import collections
import os
import shlex
import struct
import subprocess
import time

from . import disk, gpt
from .store import Progress, human, chunk_hash

HERE = os.path.dirname(os.path.abspath(__file__))
SSDPUSH = os.path.join(os.path.dirname(HERE), "ssdpush")       # tools/ssdpush: source + build.sh (binaries untracked)
LOCAL_AGENT = os.environ.get("SSDPUSH_LOCAL_AGENT") or os.path.join(SSDPUSH, "ssdpush-agent-macos")   # tests: a temp build
REC_AGENT = os.path.join(SSDPUSH, "ssdpush-agent-macos-x86_64")
REC_TOOLS = "/usr/local/ssdtools/bin"              # the Recovery's tools: ssdpush-agent, oc-launcher
REC_BUILTIN_AGENT = REC_TOOLS + "/ssdpush-agent"
REC_RUN_AGENT = "/private/var/tmp/ssdpush-agent"   # RAM (tmpfs) in the Recovery
TRANSPORT = os.environ.get("SSDIMG_TRANSPORT", "tcp")   # tcp: frames over a direct TCP connection (ssh starts
                                                        # the agent); ssh: frames through the ssh session
# root on the target's Recovery with ~/.ssh/mbp92_test; SSDIMG_TEST_KEY names another key
REC_SSH = ["-i", os.path.expanduser(os.environ.get("SSDIMG_TEST_KEY", "~/.ssh/mbp92_test")),
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=2",
           "-o", "ServerAliveCountMax=3"]   # a rebooted peer is noticed in ~6 s
MAX_READ = 8 << 20


class PushError(Exception):
    pass


class VerifyFailed(PushError):
    """verify_pass found chunks (`bad`, indexes) or other ranges (`bad_extra`, offsets) that differ."""

    def __init__(self, msg, bad, bad_extra):
        super().__init__(msg)
        self.bad, self.bad_extra = bad, bad_extra


def parse_target(spec):
    """-> (kind, addr, path): ("rec", ADDR, /dev/diskN) or ("local", None, path)"""
    if spec.startswith("local:"):
        return "local", None, spec[6:]
    head, sep, path = spec.rpartition(":")
    if not head.startswith("rec@") or len(head) == 4:
        raise PushError(f"target must be rec@ADDR:/dev/diskN (the Recovery loaded via NetBoot; your Recovery reports "
                        f"ADDR) or local:/path (got {spec!r})")
    path = path.replace("/dev/rdisk", "/dev/disk", 1)
    if not path.startswith("/dev/disk"):
        raise PushError(f"target must be rec@ADDR:/dev/diskN (got {spec!r})")
    return "rec", head[4:], path


def rec_ssh(addr, cmd, check=True, stdin=None):
    """Run cmd as root in the target's Recovery."""
    r = subprocess.run(["ssh", *REC_SSH, f"root@{addr}", cmd], capture_output=True, input=stdin,
                       text=stdin is None)
    if check and r.returncode:
        err = r.stderr if stdin is None else r.stderr.decode(errors="replace")
        raise PushError(f"Recovery ssh {addr} {cmd[:60]!r} failed: {err.strip()}")
    return r.stdout


def rec_disk_info(addr, dev):
    """diskutil facts for a whole disk in the Recovery."""
    import plistlib
    info = plistlib.loads(rec_ssh(addr, f"diskutil info -plist {shlex.quote(dev)}").encode())
    me = info["DeviceIdentifier"]
    # APFS volumes are mounted from a synthesized disk (disk3s1 ...) whose physical store is a partition of me
    owned = [me] + [c["ContainerReference"] for c in
                     plistlib.loads(rec_ssh(addr, "diskutil apfs list -plist").encode()).get("Containers", [])
                     if any(p["DeviceIdentifier"].startswith(me + "s") for p in c.get("PhysicalStores", []))]
    mounts = [l.split(" on ")[1].split(" (")[0] for l in rec_ssh(addr, "mount").splitlines()
              if any(l.startswith(f"/dev/{d}s") for d in owned)]
    return {"NAME": info["DeviceIdentifier"], "SIZE": str(info.get("TotalSize") or info.get("Size")),
            "MODEL": info.get("MediaName", "?"), "TRAN": info.get("BusProtocol", "?").lower(),
            "TYPE": "disk" if info.get("WholeDisk") else "part", "LOG-SEC": str(info.get("DeviceBlockSize")),
            "Internal": info.get("Internal"), "Virtual": info.get("VirtualOrPhysical") == "Virtual",
            "Removable": info.get("Removable") or info.get("RemovableMedia"), "mounts": mounts}


def rec_agent(addr):
    """Path of an agent in the Recovery identical to this host's x86_64 build (md5: the Recovery has no sha256
    tool), uploading it to RAM when the Recovery's own copy differs. Without a host build (tools/ssdpush/build.sh
    not run), the Recovery's own copy."""
    import hashlib
    if not os.path.isfile(REC_AGENT):
        if rec_ssh(addr, f"[ -x {REC_BUILTIN_AGENT} ] && echo yes", check=False).strip() != "yes":
            raise PushError(f"no {REC_AGENT} (run tools/ssdpush/build.sh) and no {REC_BUILTIN_AGENT} in the Recovery")
        print(f"agent: the Recovery's own {REC_BUILTIN_AGENT} (no host build at {REC_AGENT})")
        return REC_BUILTIN_AGENT
    data = open(REC_AGENT, "rb").read()
    want = hashlib.md5(data).hexdigest()
    have = rec_ssh(addr, " ".join(f"[ -x {p} ] && echo $(md5 -q {p}) {p};" for p in (REC_BUILTIN_AGENT, REC_RUN_AGENT)),
                   check=False)
    for line in have.splitlines():
        h, p = line.split()
        if h == want:
            return p
    rec_ssh(addr, f"cat > {REC_RUN_AGENT}.part && chmod 755 {REC_RUN_AGENT}.part && mv {REC_RUN_AGENT}.part {REC_RUN_AGENT}",
            stdin=data)
    if rec_ssh(addr, f"md5 -q {REC_RUN_AGENT}").strip() != want:
        raise PushError("could not install the agent in the Recovery (md5 mismatch after upload)")
    print(f"agent: installed this host's build in the Recovery:{REC_RUN_AGENT}")
    return REC_RUN_AGENT


class Link:
    """One agent session: ssh to the Recovery (or a local process) speaking the frame protocol."""

    def __init__(self, kind, host, path, image_size, chunk_size, verify, trim=False, read_only=False):
        self.sock = None
        self.p = None
        self.r = self.w = None
        self.flags = " --read-only" if read_only else ""
        try:
            if kind == "local":
                if not os.path.exists(LOCAL_AGENT):
                    raise PushError(f"{LOCAL_AGENT} missing; run tools/ssdpush/build.sh")
                cmd = [LOCAL_AGENT, path] + self.flags.split()
            else:
                argv, name, agent = ["ssh", *REC_SSH, f"root@{host}"], host, rec_agent(host)
                path = path.replace("/dev/disk", "/dev/rdisk", 1)   # raw device: uncached, no buffer cache copy
                if TRANSPORT == "tcp":
                    try:
                        self._open_tcp(argv, name, agent, path)
                    except (PushError, OSError) as e:
                        print(f"note: no TCP data channel ({e}); using the ssh session")
                cmd = argv + [f"{agent} {shlex.quote(path)}{self.flags}"]
            if not self.sock:
                self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
                self.w = self.p.stdin
                self.r = self.p.stdout
            self.send(b"SSDPUSH1" + struct.pack("<QII", image_size, chunk_size, (1 if verify else 0) | (2 if trim else 0)))
            line = self.line()
            if not line.startswith("READY"):
                self.fail(line)
            f = dict(kv.split("=") for kv in line.split()[1:])
            self.size, self.bsize = int(f["size"]), int(f["bsize"])
            self.trim = f.get("trim") == "1"
        except BaseException:
            self.abort()
            raise

    def abort(self):
        """No image frames were accepted: close every transport and reap startup."""
        if self.sock is not None:
            try:
                self.sock.shutdown(2)
            except OSError:
                pass
        streams = [self.r, self.w]
        if self.p is not None:
            streams.extend([self.p.stdin, self.p.stdout, self.p.stderr])
        for stream in streams:
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        if self.sock is not None:
            self.sock.close()
        if self.p is not None:
            if self.p.poll() is None:
                self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()

    def _open_tcp(self, argv, name, agent, path):
        """Frame stream over a direct TCP connection; ssh only starts the agent and hands it a one-time
        token (agent --tcp). Integrity does not depend on the channel: every chunk is hash-checked and
        read back by the agent."""
        import secrets
        import socket
        token = secrets.token_hex(32)
        p = subprocess.Popen(argv + [f"{agent} {shlex.quote(path)} --tcp{self.flags}"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
        try:
            p.stdin.write(token.encode() + b"\n")
            p.stdin.flush()
            line = b""
            while not line.endswith(b"\n"):
                c = p.stdout.read(1)
                if not c:
                    raise PushError(f"agent exited (status {p.wait()})")
                line += c
            line = line.decode().strip()
            if not line.startswith("LISTEN "):
                raise PushError(line)
            port = int(line.split()[1])
            infos = sorted(socket.getaddrinfo(name, port, type=socket.SOCK_STREAM), key=lambda ai: ai[0] != socket.AF_INET6)
            scope = next((ai[4][3] for ai in infos if ai[0] == socket.AF_INET6 and ai[4][3]), 0)
            err = None
            for fam, _, _, _, sa in infos:
                s = socket.socket(fam, socket.SOCK_STREAM)
                try:
                    if fam == socket.AF_INET and scope:
                        s.setsockopt(socket.IPPROTO_IP, 25, scope)   # IP_BOUND_IF: the cable, not Wi-Fi
                    s.settimeout(3)
                    s.connect(sa)
                    s.settimeout(None)
                    break
                except OSError as e:
                    err = e
                    s.close()
                    s = None
            if s is None:
                raise PushError(f"cannot connect to {name} port {port}: {err}")
            for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
                s.setsockopt(socket.SOL_SOCKET, opt, 4 << 20)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.sendall(token.encode())
        except BaseException:
            p.kill()
            p.wait()
            raise
        self.sock, self.p = s, p
        self.r = s.makefile("rb", buffering=0)

    def send(self, data):
        try:
            if self.sock:
                self.sock.sendall(data)
            else:
                self.w.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.fail(self.line())

    def line(self):
        out = b""
        while not out.endswith(b"\n"):
            c = self.r.read(1)
            if not c:
                err = self.p.stderr.read().decode() if self.p.stderr else ""
                raise PushError(f"agent exited (status {self.p.wait()}) {err}".strip())
            out += c
        return out.decode().strip()

    def exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            c = self.r.read(n - len(buf))
            if not c:
                raise PushError(f"agent exited during read (status {self.p.wait()})")
            buf += c
        return bytes(buf)

    def fail(self, line):
        raise PushError(f"agent: {line}")

    def close(self):
        try:
            self.send(b"X")
            if self.sock:
                self.sock.shutdown(1)          # SHUT_WR
            else:
                self.w.close()
        except (BrokenPipeError, OSError, PushError):
            pass
        rc = self.p.wait()                     # the agent exits after X; with --tcp, its ssh session too
        if self.sock:
            self.sock.close()
            self.p.stdin.close()
        if rc:
            raise PushError(f"agent exited with status {rc}")

    def read_at(self, off, n):
        """Unaligned read via aligned 'P' requests."""
        a = max(self.bsize, 4096)
        start, end = off // a * a, min((off + n + a - 1) // a * a, self.size)
        out = bytearray()
        pos = start
        while pos < end:
            ln = min(MAX_READ, end - pos)
            self.send(b"P" + struct.pack("<QI", pos, ln))
            out += self.exact(ln)
            pos += ln
        return bytes(out[off - start:off - start + n])


class RemoteReader:
    """disk.Reader-compatible: read(off, n) over a Link. After plan(ranges), reads of exactly those
    ranges, in that order, are pipelined: up to WINDOW bytes of requests stay in flight, so the agent
    reads the disk while earlier data is still on the wire."""

    WINDOW = 32 << 20

    def __init__(self, link, size):
        self.link, self.size = link, size
        self._plan = self._inflight = None
        self._bytes = 0

    def plan(self, ranges):
        self._plan = collections.deque(ranges)
        self._inflight = collections.deque()   # (off, n, start, [(pos, ln)...])
        self._bytes = 0
        self._fill()

    def _fill(self):
        while self._plan and self._bytes < self.WINDOW:
            off, n = self._plan.popleft()
            n = max(0, min(n, self.size - off))
            a = max(self.link.bsize, 4096)
            start, end = off // a * a, min((off + n + a - 1) // a * a, self.link.size)
            reqs, pos = [], start
            while pos < end:
                ln = min(MAX_READ, end - pos)
                self.link.send(b"P" + struct.pack("<QI", pos, ln))
                reqs.append((pos, ln))
                pos += ln
            self._inflight.append((off, n, start, reqs))
            self._bytes += end - start

    def __call__(self, off, n):
        n = max(0, min(n, self.size - off))
        if not self._inflight:
            if self._plan:
                raise PushError("read plan out of step")
            self._plan = self._inflight = None
            return self.link.read_at(off, n)
        poff, pn, start, reqs = self._inflight.popleft()
        if (poff, pn) != (off, n):
            raise PushError(f"pipelined read out of order: asked {off}+{n}, planned {poff}+{pn}")
        out = bytearray()
        for pos, ln in reqs:
            out += self.link.exact(ln)
        self._bytes -= sum(ln for _, ln in reqs)
        self._fill()
        return bytes(out[off - start:off - start + n])

    def close(self):
        pass


def start_listener(kind, host, path):
    """Start a target agent with --tcp; -> (process, token, port). Its ssh session is its lifeline."""
    import secrets
    token = secrets.token_hex(32)
    if kind == "local":
        if not os.path.exists(LOCAL_AGENT):
            raise PushError(f"{LOCAL_AGENT} missing; run tools/ssdpush/build.sh")
        cmd = [LOCAL_AGENT, path, "--tcp"]
    else:
        cmd = ["ssh", *REC_SSH, f"root@{host}",
               f"{rec_agent(host)} {shlex.quote(path.replace('/dev/disk', '/dev/rdisk', 1))} --tcp"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    try:
        p.stdin.write(token.encode() + b"\n")
        p.stdin.flush()
        line = b""
        while not line.endswith(b"\n"):
            c = p.stdout.read(1)
            if not c:
                raise PushError(f"target agent exited (status {p.wait()})")
            line += c
        line = line.decode().strip()
        if not line.startswith("LISTEN "):
            raise PushError(f"target agent: {line}")
        return p, token, int(line.split()[1])
    except BaseException:
        p.kill()
        p.wait()
        raise


class RelayLink(Link):
    """A Link to the target agent through the source laptop's agent (--relay): frames written here go to the source,
    which forwards them to the target (expanding 'S' frames from its own disk); the target's answers come back the
    same way. Same interface as Link."""

    def __init__(self, kind, host, path, src_host, src_path, image_size, chunk_size, verify, trim=False):
        self.sock = None
        self.p = self.tp = None
        self.r = self.w = None
        try:
            self.tp, token, port = start_listener(kind, host, path)
            if kind == "local":
                cmd = [LOCAL_AGENT, src_path, "--relay", "127.0.0.1", str(port)]
            else:
                agent = rec_agent(src_host)
                cmd = ["ssh", *REC_SSH, f"root@{src_host}",
                       f"{agent} {shlex.quote(src_path.replace('/dev/disk', '/dev/rdisk', 1))} --relay "
                       f"{shlex.quote(host)} {port}"]
            self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            self.w, self.r = self.p.stdin, self.p.stdout
            self.w.write(token.encode() + b"\n")
            self.send(b"SSDPUSH1" + struct.pack("<QII", image_size, chunk_size, (1 if verify else 0) | (2 if trim else 0)))
            line = self.line()
            if not line.startswith("READY"):
                self.fail(line)
            f = dict(kv.split("=") for kv in line.split()[1:])
            self.size, self.bsize = int(f["size"]), int(f["bsize"])
            self.trim = f.get("trim") == "1"
        except BaseException:
            self.abort()
            raise

    def abort(self):
        for p in (self.p, self.tp):
            if p is None:
                continue
            for stream in (p.stdin, p.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def close(self):
        try:
            self.send(b"X")
            self.w.close()
        except (BrokenPipeError, OSError, PushError):
            pass
        rc = self.p.wait()                     # the relay exits once the target has answered everything
        trc = self.tp.wait(timeout=30)         # the target agent exits after X; with it, its ssh session
        self.tp.stdin.close()
        if rc or trc:
            raise PushError(f"agent exited with status {rc or trc} ({'relay' if rc else 'target'})")


class RemoteDiskSource:
    """Another laptop's internal disk as the image (push --from-disk): the same interface as RepoSource, read through
    `link` (its agent, --read-only). `present` as `pull` finds them (the caller passes them); the hashes come from the
    source's agent ('H'), so nothing but the GPT and allocation metadata crosses to the host. `relay`: send_chunks
    sends 'S' frames, which the source's relay agent turns into data frames."""

    relay = True

    def __init__(self, link, cs, present):
        self.link, self.cs, self.sector = link, cs, link.bsize
        self.size = link.size
        self.present = set(present)
        self.read = RemoteReader(link, link.size)
        self.man = {}
        idx = sorted(self.present)
        prog = Progress("hash (on the source)")
        zero = {}
        for k in range(0, len(idx), 4096):
            batch = idx[k:k + 4096]
            body = bytearray(b"H" + struct.pack("<I", len(batch)))
            for i in batch:
                body += struct.pack("<QI", i * cs, min(cs, self.size - i * cs))
            link.send(bytes(body))
            got = link.exact(20 * len(batch))
            for j, i in enumerate(batch):
                n = min(cs, self.size - i * cs)
                if n not in zero:
                    zero[n] = bytes.fromhex(chunk_hash(bytes(n)))
                h = got[20 * j:20 * j + 20]
                if h != zero[n]:
                    self.man[i] = h.hex()
            prog(min(k + 4096, len(idx)), len(idx))

    def blob(self, i):
        raise PushError("a disk source sends S frames, not blobs")



def _confirm(text, want):
    ans = input(f"type {want!r} to continue: ").strip()
    if ans != want:
        raise PushError("aborted")


def guard(kind, host, path, usb=None):
    """Print what is about to be overwritten / read; refuse anything but the target's internal physical disk, or with
    `usb` (an IDENTITY, see parse_identity) anything but that external drive."""
    if kind == "local":
        if usb:
            raise PushError("--usb needs a rec@ADDR:/dev/diskN target")
        print(f"target: local file {path}  {human(os.path.getsize(path))}")
        return path
    if usb:
        return guard_usb(host, path, parse_identity(usb))
    return guard_rec(host, path)


# ---------------------------------------------------------------- --usb: an external drive on the laptop
# IDENTITY = "media=MEDIA NAME,size=BYTES,lun=N" names exactly one drive: diskutil's MediaName (vendor and product as
# the drive reports them), its byte size, and the SCSI logical unit number in its USB enclosure ("-" when it has
# none). The LUN is m27-accept's notion (tools/m27-accept USB_LIST): a two-bay enclosure reports no per-drive serial
# and its bays are the size of the internal disk, so the LUN is what tells them apart.
USB_KEYS = ("media", "size", "lun")
USB_ZERO_HEAD = 4 << 20                # zeroed before a USB write: the MBR, the primary GPT and what follows
USB_ZERO_TAIL = 8192 * 512             # and the end of the drive (the backup GPT), as m27-accept's unboot_usb
LUN_CMD = ("ioreg -l -w0 -p IOService 2>/dev/null | "
           "grep -E '\"SCSI Logical Unit Number\" ?=|\"BSD Name\" = \"disk[0-9]+\"$'")


def parse_identity(text):
    """"media=...,size=...,lun=..." -> {media, size, lun} (strings); every key exactly once."""
    out = {}
    for part in text.split(","):
        k, sep, v = part.partition("=")
        k = k.strip()
        if not sep or k not in USB_KEYS or k in out:
            raise PushError(f"--usb {text!r}: expected media=NAME,size=BYTES,lun=N|- (each once)")
        out[k] = v.strip()
    missing = [k for k in USB_KEYS if k not in out]
    if missing or not out["size"].isdigit() or not (out["lun"] == "-" or out["lun"].isdigit()) or not out["media"]:
        raise PushError(f"--usb {text!r}: expected media=NAME,size=BYTES,lun=N|- (missing or bad: "
                        f"{', '.join(missing) or 'a value'})")
    return out


def lun_of(text, name):
    """SCSI logical unit number of BSD disk `name` from the lines LUN_CMD prints ("-" when none): the last LUN
    listed before its "BSD Name" line, as m27-accept's USB_LIST reads it."""
    import re
    lun = None
    for line in text.splitlines():
        m = re.search(r'"SCSI Logical Unit Number" ?= ?(\d+)', line)
        if m:
            lun = m[1]
            continue
        m = re.search(r'"BSD Name" = "(disk\d+)"$', line.strip())
        if m and m[1] == name:
            return lun or "-"
    return "-"


def usb_guard(info, lun, ident, dev="disk"):
    """--usb target check on `diskutil info -plist` facts (a dict) and the drive's LUN: a physical, external whole
    disk whose media name, byte size and LUN equal `ident` exactly. -> a one-line description; PushError otherwise
    (the internal disk, a partition, a disk image or RAM disk, another drive)."""
    bus = info.get("BusProtocol", "?")
    removable = any(info.get(k) for k in ("Removable", "RemovableMedia", "Ejectable"))
    why = []
    if not info.get("WholeDisk"):
        why.append("a partition, not a whole disk")
    if info.get("VirtualOrPhysical") != "Physical":
        why.append(f"not a physical disk ({info.get('VirtualOrPhysical', '?')}: a disk image or RAM disk?)")
    if info.get("Internal") is not False:
        why.append("an internal disk" if info.get("Internal") else "not reported as external")
    if bus not in disk.EXTERNAL_BUSES and not removable:
        why.append(f"on the {bus} bus and not removable")
    if why:
        raise PushError(f"--usb: {dev} refused: " + "; ".join(why))
    got = {"media": str(info.get("MediaName") or "").strip(), "size": str(info.get("TotalSize") or info.get("Size")),
           "lun": lun}
    diff = [f"{k} {got[k]!r} (expected {ident[k]!r})" for k in USB_KEYS if got[k] != ident[k]]
    if diff:
        raise PushError(f"--usb: {dev} is not the expected drive: " + "; ".join(diff))
    return f"{got['media']}  {human(int(got['size']))} ({got['size']} bytes)  LUN {lun}  {bus}"


def guard_usb(addr, path, ident):
    """Recovery target with --usb: exactly the drive `ident` names (usb_guard); its volumes are unmounted first."""
    import plistlib
    info = plistlib.loads(rec_ssh(addr, f"diskutil info -plist {shlex.quote(path)}").encode())
    lun = lun_of(rec_ssh(addr, LUN_CMD, check=False), info.get("DeviceIdentifier", ""))
    what = usb_guard(info, lun, ident, path)
    print(f"target: Recovery {addr}:{path}  USB drive {what}  {info.get('DeviceBlockSize')}-byte sectors")
    i = rec_disk_info(addr, path)
    if i["mounts"]:
        print(f"unmounting {len(i['mounts'])} volume(s) on the USB drive: {', '.join(i['mounts'])}")
        rec_ssh(addr, f"diskutil unmountDisk force {shlex.quote(path)}", check=False)
        i = rec_disk_info(addr, path)
        if i["mounts"]:
            raise PushError(f"{path} still has mounted volumes: {', '.join(i['mounts'])}")
    return i["NAME"]


def usb_zero_ranges(target_size):
    """[(off, len)] zeroed before a USB write so macOS finds no partition table on a half-written drive."""
    head = min(USB_ZERO_HEAD, target_size)
    tail = max(head, target_size - USB_ZERO_TAIL)
    return [(0, head)] + ([(tail, target_size - tail)] if tail < target_size else [])


def usb_rewrite(present, target_size, cs):
    """Present chunks inside the zeroed ranges: written whatever the delta check found."""
    return {i for i in present for off, n in usb_zero_ranges(target_size) if i * cs < off + n and (i + 1) * cs > off}


def guard_rec(addr, path, role="target"):
    """Recovery target: a physical, internal, non-removable whole disk (never a USB disk, a disk image or the RAM
    disk the Recovery runs from). The Recovery mounts the internal volumes at boot, so they are unmounted first
    (`diskutil unmountDisk`); anything still mounted afterwards is refused."""
    i = rec_disk_info(addr, path)
    print(f"{role}: Recovery {addr}:{path}  {i['MODEL']}  {human(int(i['SIZE']))}  {i['TRAN']}  "
          f"{i['LOG-SEC']}-byte sectors")
    if i["TYPE"] != "disk":
        raise PushError(f"{path} is a partition, not a whole disk")
    if i["Virtual"] or not i["Internal"] or i["Removable"]:
        raise PushError(f"{path} is not a physical internal fixed disk (virtual={i['Virtual']} internal={i['Internal']} "
                        f"removable={i['Removable']}): refusing (a USB disk or the Recovery's own RAM disk?)")
    if i["mounts"]:
        print(f"unmounting {len(i['mounts'])} volume(s) the Recovery mounted: {', '.join(i['mounts'])}")
        rec_ssh(addr, f"diskutil unmountDisk {shlex.quote(path)}", check=False)
        i = rec_disk_info(addr, path)
        if i["mounts"]:
            raise PushError(f"{path} still has mounted volumes: {', '.join(i['mounts'])}")
    return i["NAME"]


# ---------------------------------------------------------------- push

class RepoSource:
    """The chunks of one commit of an image repository: `man` = chunk index -> BLAKE2b hex of every non-zero chunk,
    `present` = every chunk the image puts on the target (data + allocated zero chunks), `blob(i)` = the stored zstd
    frame of chunk i."""

    def __init__(self, repo, man, meta, present):
        self.repo, self.man, self.present = repo, man, set(present)
        self.size, self.cs, self.sector = meta["size"], meta["chunk_size"], meta["sector_size"]
        if self.cs % self.sector or self.size % self.sector:   # the agent reads the target in whole sectors only
            raise PushError(f"image chunk size {self.cs} / size {self.size} is not a whole number of "
                            f"{self.sector}-byte sectors")
        self.read = repo.reader(man, self.size)

    def blob(self, i):
        with open(self.repo.obj_path(self.man[i]), "rb") as f:
            return f.read()


class RawSource:
    """A raw disk image file pushed without a repository (`push --image`): the same interface as RepoSource. Every
    present chunk is read and hashed up front (the delta query needs the hashes) and compressed again when sent; a
    partial last chunk is hashed zero-padded, as the store does."""

    def __init__(self, path, cs, present, sector=512):
        from concurrent.futures import ThreadPoolExecutor
        self.path, self.cs, self.sector = path, cs, sector
        self.size = os.path.getsize(path)
        self.present = set(present)
        self.fd = os.open(path, os.O_RDONLY)
        self.read = disk.Reader(path, align=4096, size=self.size)
        zero = bytes(cs)

        def work(batch):
            return [(i, chunk_hash(d)) for i in batch if (d := self.chunk(i)) != zero]
        idx = sorted(self.present)
        prog, self.man = Progress("hash"), {}
        with ThreadPoolExecutor(min(8, os.cpu_count() or 4)) as ex:
            for k, part in enumerate(ex.map(work, [idx[j:j + 64] for j in range(0, len(idx), 64)])):
                self.man.update(part)
                prog(min((k + 1) * 64, len(idx)), len(idx))

    def chunk(self, i):
        return os.pread(self.fd, self.cs, i * self.cs).ljust(self.cs, b"\0")

    def blob(self, i):
        from compression import zstd
        return zstd.compress(self.chunk(i), level=1)


def overlay_hashes(present, cs, overlay, read):
    """chunk -> BLAKE2b of what a present chunk holds after the relocated GPT writes (`overlay`) went over it"""
    return {i: bytes.fromhex(chunk_hash(_overlay(i * cs, read(i * cs, cs), overlay))) for i in sorted(present)
            if any(po < (i + 1) * cs and po + len(pd) > i * cs for po, pd in overlay)}


def delta_chunks(link, man, present, cs, size, expect=None):
    """Compare target bytes (agent 'Q'); -> the chunks that differ. `expect`: chunk -> hash overriding the
    manifest's (chunks the relocated GPT goes over)."""
    expect = expect or {}
    zero_hash = bytes.fromhex(chunk_hash(bytes(cs)))
    tail = (size + cs - 1) // cs - 1 if size % cs else None
    q = [i for i in sorted(present) if i != tail]
    differ = {tail} & set(present)
    for start in range(0, len(q), 4096):
        batch = q[start:start + 4096]
        body = bytearray(b"Q" + struct.pack("<I", len(batch)))
        for i in batch:
            body += struct.pack("<QI", i * cs, cs)
            body += expect.get(i) or (bytes.fromhex(man[i]) if i in man else zero_hash)
        link.send(bytes(body))
        count = struct.unpack("<I", link.exact(4))[0]
        if count > len(batch):
            raise PushError("invalid delta reply: mismatch count exceeds query")
        bad = struct.unpack(f"<{count}I", link.exact(4 * count)) if count else ()
        if len(set(bad)) != len(bad) or any(i >= len(batch) for i in bad):
            raise PushError("invalid delta reply: duplicate or out-of-range mismatch index")
        differ.update(batch[i] for i in bad)
    return sorted(differ)


def plan_delta(link, src, raw, st):
    """S25 "write only what changed": -> the chunks to write. A target that does not look like an earlier write of
    this image family gets a full write: its primary GPT (MBR, header, entries as the push would leave them) differs
    from the image's, or more than half of the present chunks differ (then the 'Q' answers are not re-asked)."""
    t0 = time.time()
    head = src.sector * gpt.GPT(src.read, src.sector).first_usable
    gpt_same = all(link.read_at(off, len(data)) == data for off, data in raw if off < head)
    st["seconds"]["gpt-check"] = round(time.time() - t0, 2)
    every = sorted(src.present)
    if not gpt_same:
        print("target does not match the image (its primary GPT differs): full write")
        st["mode"] = "full (target does not match)"
        return every
    t0 = time.time()
    print(f"delta: asking the agent to hash {len(src.present)} allocated chunks already on the target ...")
    todo = delta_chunks(link, src.man, src.present, src.cs, src.size,
                        expect=overlay_hashes(src.present, src.cs, raw, src.read))
    tail = (src.size + src.cs - 1) // src.cs - 1 if src.size % src.cs else None
    st["queried"] = len(src.present) - (tail in src.present)
    st["matched"] = len(src.present) - len(todo)
    st["seconds"]["query"] = round(time.time() - t0, 2)
    print(f"delta: {len(todo)} of {len(src.present)} chunks differ ({st['seconds']['query']:.0f}s)")
    if 2 * (len(todo) - (tail in src.present)) > st["queried"]:
        print(f"target does not match the image ({len(todo)} of {len(src.present)} chunks differ): full write")
        st["mode"] = "full (target does not match)"
        return every
    st["mode"] = "delta"
    return todo


def send_chunks(link, src, chunks, label="push"):
    """D frames (zstd chunk + hash) or Z frames (allocated all-zero chunk); -> (bytes written, bytes on the wire)"""
    sent = wire = 0
    prog = Progress(label)
    for n, i in enumerate(chunks):
        off = i * src.cs
        wlen = min(src.cs, src.size - off)
        if i in src.man and getattr(src, "relay", False):
            frame = b"S" + struct.pack("<QI", off, wlen) + bytes.fromhex(src.man[i])
        elif i in src.man:
            blob = src.blob(i)
            frame = (b"D" + struct.pack("<QI", off, wlen) + bytes.fromhex(src.man[i]) + struct.pack("<I", len(blob))
                     + blob)
        else:
            frame = b"Z" + struct.pack("<QI", off, wlen)
        link.send(frame)
        sent += wlen
        wire += len(frame)
        if n % 256 == 0:
            prog(n, len(chunks))
    prog(len(chunks), len(chunks))
    return sent, wire


def flush(link):
    link.send(b"F")
    ok = link.line()
    if not ok.startswith("OK"):
        link.fail(ok)
    return ok


def send_raw(link, pieces):
    for off, data in pieces:
        link.send(b"R" + struct.pack("<QI", off, len(data)) + data)


def write_pass(link, src, todo, raw, st, phase="write", usb=False):
    """Write `todo` chunks, then the relocated GPT (`raw`), and flush. usb: in m27-accept's order, so macOS never
    mounts a half-written drive: zero the partition tables (usb_zero_ranges) and flush, write everything but the
    chunks and pieces before the first usable LBA, flush, then those (the MBR and primary GPT) last."""
    t0 = time.time()
    if not usb:
        sent, wire = send_chunks(link, src, todo)
        send_raw(link, raw)
    else:
        head = src.sector * gpt.GPT(src.read, src.sector).first_usable
        zeros = bytes(4 << 20)
        for off, n in usb_zero_ranges(link.size):
            send_raw(link, [(o, zeros[:min(len(zeros), off + n - o)]) for o in range(off, off + n, len(zeros))])
        flush(link)
        print(f"usb: partition tables zeroed ({', '.join(f'{human(n)} at {off}' for off, n in usb_zero_ranges(link.size))})")
        todo = sorted(set(todo) | usb_rewrite(src.present, link.size, src.cs))
        sent, wire = send_chunks(link, src, [i for i in todo if i * src.cs >= head])
        send_raw(link, [(off, data) for off, data in raw if off >= head])
        flush(link)
        s2, w2 = send_chunks(link, src, [i for i in todo if i * src.cs < head], label="push (primary GPT last)")
        send_raw(link, [(off, data) for off, data in raw if off < head])
        sent, wire = sent + s2, wire + w2
    ok = flush(link)
    dt = time.time() - t0
    st["written"] += len(todo)
    st["bytes"] += sent
    st["seconds"][phase] = round(dt, 2)
    print(f"pushed {human(sent)} ({len(todo)} chunks, {human(wire)} on the wire) in {dt:.0f}s "
          f"({human(sent / max(dt, 1e-3))}/s); agent: {ok[3:]}")


def summary(st):
    """One line per push with the S25 counts and the seconds of each phase."""
    secs = " ".join(f"{k}={v}" for k, v in st["seconds"].items())
    print(f"push summary: mode={st['mode']!r} queried={st['queried']} matched={st['matched']} "
          f"written={st['written']} bytes={st['bytes']} fallback={st['fallback']} seconds: {secs}")


def push(src, a):
    """Write `src` (RepoSource or RawSource) to a.target. S25: in delta mode (default) only the chunks that differ
    are written, unless the target does not look like an earlier write of the image (plan_delta: full write);
    every present chunk is verified afterwards, and a failed verify after a delta write falls back once to a full
    write and a second verify. A failed verify after a full write is an error."""
    delta = getattr(a, "delta", True)
    if delta and a.no_verify:
        raise PushError("delta push requires full readback; --no-verify is only available with --full")
    kind, host, path = parse_target(a.target)
    size, cs, sector = src.size, src.cs, src.sector
    usb = getattr(a, "usb", None)
    pin = guard(kind, host, path, usb)
    if not a.yes:
        print(f"\nALL DATA ON {a.target} WILL BE DESTROYED.")
        _confirm(a.target, pin)

    mode = "none" if a.no_verify else getattr(a, "verify", None) or "end"
    # --usb: zero chunks are written, not TRIMmed (a USB bridge need not read zeros after UNMAP)
    trim = not (getattr(a, "no_trim", False) or usb)
    if getattr(src, "relay", False):
        if usb:
            raise PushError("--from-disk and --usb exclude each other")
        link = RelayLink(kind, host, path, a.src_host, a.src_path, size, cs, verify=mode == "inline", trim=trim)
    else:
        link = Link(kind, host, path, size, cs, verify=mode == "inline", trim=trim)
    st = {"mode": "delta" if delta else "full", "queried": 0, "matched": 0, "written": 0, "bytes": 0,
          "fallback": 0, "seconds": {}}
    try:
        print(f"agent ready: target {human(link.size)}, {link.bsize}-byte sectors; verify: {mode}; "
              f"zero chunks: {'TRIM' if link.trim else 'written'}")
        if link.bsize != sector:
            raise PushError(f"target sector size {link.bsize} != image sector size {sector}")
        nchunks = (size + cs - 1) // cs
        tail = nchunks - 1 if size % cs else None
        # Backup GPT at the end of the target, primary header updated to match.
        raw = list(gpt.GPT(src.read, sector).relocated(link.size // sector))

        t_start = time.time()
        todo = plan_delta(link, src, raw, st) if delta else sorted(src.present)
        write_pass(link, src, todo, raw, st, usb=bool(usb))
        if mode == "inline" and not delta:
            print("verify: every chunk hash-checked and read back from the target right after writing: OK")
        elif mode == "end" or delta:
            extra = raw + ([(tail * cs, src.read(tail * cs, size - tail * cs))]
                           if tail is not None and tail in src.present else [])
            t0 = time.time()
            try:
                # Recheck skipped chunks after writes too: initial equality is not final readback.
                verify_pass(link, src.man, src.present, cs, size, extra, overlay=raw, read=src.read)
            except VerifyFailed as e:
                st["seconds"]["verify"] = round(time.time() - t0, 2)
                if st["mode"] != "delta":
                    raise
                print(f"{e}\nverify failed after the delta write: full write of all {len(src.present)} present "
                      "chunks, then verify again")
                st["fallback"] = 1
                write_pass(link, src, sorted(src.present), raw, st, phase="fallback-write", usb=bool(usb))
                t0 = time.time()
                try:
                    verify_pass(link, src.man, src.present, cs, size, extra, overlay=raw, read=src.read)
                finally:
                    st["seconds"]["fallback-verify"] = round(time.time() - t0, 2)
            else:
                st["seconds"]["verify"] = round(time.time() - t0, 2)
            print(f"push + verify: {time.time() - t_start:.0f}s in total{' (with the delta check)' if delta else ''}")
    finally:
        summary(st)
        link.close()
    if kind != "rec":
        return
    if usb:
        # the laptop's internal disk is not touched: its launcher stays as it is; the drive keeps the image's size
        print("usb: written and verified; OpenCore launcher of the internal disk and APFS container size left as they are")
        return
    if not getattr(a, "keep_launcher", False):
        # OCLP's LauncherOption=Full makes OpenCore the firmware's first boot entry on every boot, so restarts would
        # stay in macOS; Disabled keeps the firmware's default (NetBoot) first.
        disable_launcher(host)
    if link.size > size and not getattr(a, "no_grow", False):
        # a smaller image would leave the rest of the disk unused; the Recovery grows the container in place
        out = rec_ssh(host, f"p=$(diskutil list {shlex.quote(path)} | awk '/Apple_APFS/{{print $NF; exit}}'); "
                            "[ -n \"$p\" ] && diskutil apfs resizeContainer \"$p\" 0 >/dev/null 2>&1 && "
                            "echo \"GROWN $p $(diskutil info \"$p\" | awk -F': *' '/Disk Size/{print $2}')\"",
                      check=False)
        grown = [l for l in out.splitlines() if l.startswith("GROWN ")]
        if not grown:
            raise PushError("image written and verified, but growing the APFS container failed")
        print(f"grow: APFS container {grown[0][6:]}")
    elif link.size > size:
        print(f"note: target is {human(link.size - size)} larger than the image; the APFS container "
              "keeps the image's size until grown (diskutil apfs resizeContainer <part> 0)")


def disable_launcher(addr):
    """OpenCore's launcher Disabled on the internal ESP (the Recovery's oc-launcher). A written image is not safe to
    boot if this failed: restarts would stay in it instead of returning to NetBoot."""
    try:
        result = subprocess.run(["ssh", *REC_SSH, "root@" + addr, REC_TOOLS + "/oc-launcher Disabled"],
                                capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as error:
        raise PushError("image written, launcher state unverified; do not boot: " + str(error)) from error
    message = (result.stdout or result.stderr).strip()
    if result.returncode:
        raise PushError("image written, launcher preparation failed; do not boot: " +
                        (message or str(result.returncode)))
    print(message or "launcher helper completed: Disabled")


# ---------------------------------------------------------------- verify (whole image, after the fact)

def _overlay(off, data, pieces):
    """data (read at off) with the parts of `pieces` [(off, bytes)] that fall inside it written over it"""
    out = None
    for po, pd in pieces:
        lo, hi = max(off, po), min(off + len(data), po + len(pd))
        if lo < hi:
            out = out or bytearray(data)
            out[lo - off:hi - off] = pd[lo - po:hi - po]
    return bytes(out) if out is not None else data


def verify_pass(link, man, present, cs, size, extra=(), overlay=(), read=None):
    """Re-read every chunk the image puts on the target, uncached, and compare its BLAKE2b with the
    image's (agent 'Q', 4 threads, next batch queued while one runs); `extra` = [(off, bytes)] pieces
    compared byte for byte (relocated GPT, a partial last chunk). `overlay` = the relocated GPT writes that
    went over the image's data after it (MBR, primary header and entries in chunk 0, the backup at the end
    of an equal-size disk): chunks and pieces they touch are expected with them applied (read = the
    image's reader, needed for those chunks). Raises PushError on any difference."""
    zero_hash = bytes.fromhex(chunk_hash(bytes(cs)))
    tail = (size + cs - 1) // cs - 1 if size % cs else None
    q = [i for i in sorted(present) if i != tail]
    expect = overlay_hashes(q, cs, overlay, read)         # chunk -> hash after the GPT overlay
    extra = [(off, _overlay(off, data, overlay)) for off, data in extra]
    B = 4096
    batches = [q[k:k + B] for k in range(0, len(q), B)]

    def ask(batch):
        body = bytearray(b"Q" + struct.pack("<I", len(batch)))
        for i in batch:
            body += struct.pack("<QI", i * cs, cs) + expect.get(i, bytes.fromhex(man[i]) if i in man else zero_hash)
        link.send(bytes(body))

    t0, done, bad = time.time(), 0, []
    prog = Progress("verify")
    if batches:
        ask(batches[0])
    for k, batch in enumerate(batches):
        if k + 1 < len(batches):
            ask(batches[k + 1])
        m = struct.unpack("<I", link.exact(4))[0]
        bad += [batch[j] for j in (struct.unpack(f"<{m}I", link.exact(4 * m)) if m else ())]
        done += len(batch)
        prog(done, len(q))
    bad_extra = []
    for off, data in extra:
        if link.read_at(off, len(data)) != data:
            bad_extra.append(off)
    dt = time.time() - t0
    if bad or bad_extra:
        where = ", ".join(f"chunk {i} (offset {i * cs})" for i in bad[:5]) or ", ".join(map(str, bad_extra[:5]))
        raise VerifyFailed(f"VERIFY FAILED: {len(bad)} chunks and {len(bad_extra)} other ranges differ from the image "
                           f"(first: {where})", bad, bad_extra)
    print(f"verify: {len(q)} chunks ({human(len(q) * cs)}) re-read from the target and hash-checked, "
          f"{len(extra)} other ranges compared, in {dt:.0f}s ({human(len(q) * cs / max(dt, 1e-3))}/s): all match")


def verify(src, a):
    """`ssdimg verify`: read-only check that the target holds the image (RepoSource or RawSource)."""
    kind, host, path = parse_target(a.target)
    size, cs, sector = src.size, src.cs, src.sector
    guard(kind, host, path, getattr(a, "usb", None))
    link = Link(kind, host, path, 0, cs, verify=False)
    if link.size < size:
        raise PushError(f"target ({human(link.size)}) is smaller than the image ({human(size)})")
    read = src.read
    g = gpt.GPT(read, sector)
    tail = (size + cs - 1) // cs - 1 if size % cs else None
    raw = list(g.relocated(link.size // sector))
    extra = raw + ([(tail * cs, read(tail * cs, size - tail * cs))] if tail is not None and tail in src.present else [])
    verify_pass(link, src.man, src.present, cs, size, extra, overlay=raw, read=read)
    link.close()


# ---------------------------------------------------------------- pull, --from-disk

def disk_source_open(spec, target_spec, cs):
    """push --from-disk: (kind, host, path, read-only Link) of the source laptop's disk. The same guard as a target
    (a physical internal whole disk; the Recovery's mounts are unmounted, so it cannot change during the push)."""
    kind, host, path = parse_target(spec)
    tkind, thost, tpath = parse_target(target_spec)
    if kind != tkind:
        raise PushError("--from-disk and the target must both be rec@ (or both local: in tests)")
    if (host, path) == (thost, tpath) or (kind == "rec" and host == thost):
        raise PushError("--from-disk names the target itself")
    if kind == "local":
        print(f"source: local file {path}  {human(os.path.getsize(path))}")
    else:
        guard_rec(host, path, role="source")
    link = Link(kind, host, path, 0, cs, verify=False, read_only=True)
    print(f"source agent ready (read-only): {human(link.size)}, {link.bsize}-byte sectors")
    return kind, host, path, link


def pull_open(a):
    kind, host, path = parse_target(a.target)
    guard(kind, host, path)
    link = Link(kind, host, path, 0, 1 << 18, verify=False)
    print(f"agent ready: source {human(link.size)}, {link.bsize}-byte sectors")
    return link
