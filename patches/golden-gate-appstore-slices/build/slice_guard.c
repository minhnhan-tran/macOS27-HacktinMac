/* m27-slice-guard: after an App Store install or update, report the Mach-O files of each App Store app that lack an
 * x86_64 slice while the app's main executable is universal (x86_64 + arm64). Run by the LaunchDaemon
 * org.m27.slice-guard (WatchPaths /Applications, StartInterval, RunAtLoad).
 *
 *   m27-slice-guard [--all] [--state FILE] [--log FILE] [--settle S] [--wait S] [ROOT...]   (default ROOT /Applications)
 *
 * A bundle is ROOT/NAME.app with Contents/_MASReceipt/receipt (any NAME.app with --all). It is scanned when its
 * receipt, Info.plist or main executable changed since the state file's record of it. A bundle with a file newer than
 * --settle seconds is still being written: it is retried for up to --wait seconds, then left for the next run.
 * Each scan writes one summary line and one line per Mach-O without x86_64 (only when the main executable is
 * universal) to the log file and to os_log (subsystem org.m27.slice-guard).
 */
#include <CoreFoundation/CoreFoundation.h>
#include <errno.h>
#include <fcntl.h>
#include <fts.h>
#include <libgen.h>
#include <limits.h>
#include <os/log.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define CPU_X86_64 0x01000007u
#define CPU_ARM64  0x0100000cu
enum { A_X86_64 = 1, A_ARM64 = 2, A_OTHER = 4 };

static const char *log_path = "/Library/Logs/m27/slice-guard.log";
static const char *state_path = "/private/var/db/m27/slice-guard.state";
static FILE *log_file;
static os_log_t oslog;

static uint32_t be32(const unsigned char *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static uint32_t le32(const unsigned char *p) { return (uint32_t)p[3] << 24 | p[2] << 16 | p[1] << 8 | p[0]; }

static int arch_bit(uint32_t cputype)
{
    return cputype == CPU_X86_64 ? A_X86_64 : cputype == CPU_ARM64 ? A_ARM64 : A_OTHER;
}

/* Architectures of a Mach-O file as A_* bits; 0 if the file is not Mach-O. Java class files share the fat magic and
 * are told apart by the count (their minor/major version is >= 45). */
static int macho_archs(const char *path, char *names, size_t names_len)
{
    unsigned char h[8 + 16 * 32];
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    if (fd < 0)
        return 0;
    ssize_t n = read(fd, h, sizeof h);
    close(fd);
    if (n < 8)
        return 0;
    int bits = 0;
    names[0] = 0;
    uint32_t magic = be32(h);
    uint32_t types[16];
    int count = 0;
    if (magic == 0xcafebabe || magic == 0xcafebabf) {
        uint32_t nfat = be32(h + 4);
        size_t entry = magic == 0xcafebabe ? 20 : 32;
        if (nfat == 0 || nfat > 16 || (size_t)n < 8 + nfat * entry)
            return 0;
        for (uint32_t i = 0; i < nfat; i++)
            types[count++] = be32(h + 8 + i * entry);
    } else if (le32(h) == 0xfeedface || le32(h) == 0xfeedfacf) {
        types[count++] = le32(h + 4);
    } else if (magic == 0xfeedface || magic == 0xfeedfacf) {
        types[count++] = be32(h + 4);
    } else {
        return 0;
    }
    for (int i = 0; i < count; i++) {
        bits |= arch_bit(types[i]);
        const char *name = types[i] == CPU_X86_64 ? "x86_64" : types[i] == CPU_ARM64 ? "arm64" : "other";
        if (strlen(names) + strlen(name) + 2 < names_len) {
            if (names[0])
                strcat(names, ",");
            strcat(names, name);
        }
    }
    return bits;
}

static void emit(int finding, const char *fmt, ...)
{
    char line[4096];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    char stamp[32];
    time_t now = time(NULL);
    strftime(stamp, sizeof stamp, "%Y-%m-%dT%H:%M:%SZ", gmtime(&now));
    if (log_file) {
        fprintf(log_file, "%s %s\n", stamp, line);
        fflush(log_file);
    }
    printf("%s\n", line);
    if (finding)
        os_log_error(oslog, "%{public}s", line);
    else
        os_log(oslog, "%{public}s", line);
}

/* Info.plist string value KEY into OUT, "" if missing. */
static void plist_string(CFDictionaryRef info, const char *key, char *out, size_t len)
{
    out[0] = 0;
    if (!info)
        return;
    CFStringRef k = CFStringCreateWithCString(NULL, key, kCFStringEncodingUTF8);
    CFTypeRef v = CFDictionaryGetValue(info, k);
    CFRelease(k);
    if (v && CFGetTypeID(v) == CFStringGetTypeID())
        CFStringGetCString(v, out, (CFIndex)len, kCFStringEncodingUTF8);
}

static void one_word(char *s)   /* a version as one field of the log line */
{
    for (; *s; s++)
        if (*s == '\n' || *s == ' ' || *s == '\t')
            *s = '_';
}

static CFDictionaryRef read_info(const char *bundle)
{
    char path[PATH_MAX];
    snprintf(path, sizeof path, "%s/Contents/Info.plist", bundle);
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    unsigned char *buf = NULL;
    size_t len = 0, cap = 0;
    for (;;) {
        if (len == cap) {
            cap = cap ? cap * 2 : 65536;
            if (cap > (16u << 20) || !(buf = reallocf(buf, cap)))
                break;
        }
        size_t got = fread(buf + len, 1, cap - len, f);
        if (!got)
            break;
        len += got;
    }
    fclose(f);
    if (!buf)
        return NULL;
    CFDataRef data = CFDataCreate(NULL, buf, (CFIndex)len);
    free(buf);
    CFPropertyListRef plist = CFPropertyListCreateWithData(NULL, data, kCFPropertyListImmutable, NULL, NULL);
    CFRelease(data);
    if (plist && CFGetTypeID(plist) != CFDictionaryGetTypeID()) {
        CFRelease(plist);
        return NULL;
    }
    return plist;
}

/* ---- state: one line per bundle, "KEY PATH", KEY made of the stat of receipt, Info.plist and main executable ---- */
struct entry { char *path; char *key; };
static struct entry *state;
static size_t state_n, state_cap;

static void state_set(const char *path, const char *key)
{
    for (size_t i = 0; i < state_n; i++)
        if (!strcmp(state[i].path, path)) {
            free(state[i].key);
            state[i].key = strdup(key);
            return;
        }
    if (state_n == state_cap) {
        state_cap = state_cap ? state_cap * 2 : 64;
        state = realloc(state, state_cap * sizeof *state);
    }
    state[state_n].path = strdup(path);
    state[state_n++].key = strdup(key);
}

static const char *state_get(const char *path)
{
    for (size_t i = 0; i < state_n; i++)
        if (!strcmp(state[i].path, path))
            return state[i].key;
    return NULL;
}

static void state_load(void)
{
    FILE *f = fopen(state_path, "r");
    if (!f)
        return;
    char line[PATH_MAX + 256];
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\n")] = 0;
        char *sp = strchr(line, ' ');
        if (!sp)
            continue;
        *sp = 0;
        state_set(sp + 1, line);
    }
    fclose(f);
}

static void state_save(void)
{
    char tmp[PATH_MAX];
    snprintf(tmp, sizeof tmp, "%s.new", state_path);
    FILE *f = fopen(tmp, "w");
    if (!f) {
        emit(1, "error=state-write path=%s errno=%d", tmp, errno);
        return;
    }
    for (size_t i = 0; i < state_n; i++)
        fprintf(f, "%s %s\n", state[i].key, state[i].path);
    if (fclose(f) == 0)
        rename(tmp, state_path);
}

static void stat_key(const char *path, char *out, size_t len)
{
    struct stat st;
    size_t used = strlen(out);
    if (stat(path, &st) == 0)
        snprintf(out + used, len - used, "%s%lld.%ld:%lld:%llu", used ? "/" : "", (long long)st.st_mtimespec.tv_sec,
                 st.st_mtimespec.tv_nsec, (long long)st.st_size, (unsigned long long)st.st_ino);
    else
        snprintf(out + used, len - used, "%s-", used ? "/" : "");
}

/* ---- one bundle ---- */
struct finding { char *rel; char archs[64]; };

/* 1 scanned, 0 still being written (newest file younger than SETTLE seconds) */
static int scan(const char *bundle, const char *main_rel, const char *version, int settle)
{
    char *paths[] = {(char *)bundle, NULL};
    FTS *fts = fts_open(paths, FTS_PHYSICAL | FTS_NOCHDIR | FTS_XDEV, NULL);
    if (!fts) {
        emit(1, "app=%s error=walk errno=%d", bundle, errno);
        return 1;
    }
    size_t base = strlen(bundle) + 1, machos = 0, n_find = 0, cap_find = 0;
    struct finding *find = NULL;
    int main_bits = 0;
    char main_names[64] = "none";
    time_t newest = 0, now = time(NULL);
    FTSENT *e;
    while ((e = fts_read(fts))) {
        if (e->fts_info == FTS_D || e->fts_info == FTS_F || e->fts_info == FTS_SL) {
            if (e->fts_statp->st_mtimespec.tv_sec > newest)
                newest = e->fts_statp->st_mtimespec.tv_sec;
        }
        if (e->fts_info != FTS_F || e->fts_statp->st_size < 28)
            continue;
        char names[64];
        int bits = macho_archs(e->fts_accpath, names, sizeof names);
        if (!bits)
            continue;
        machos++;
        const char *rel = e->fts_path + base;
        if (!strcmp(rel, main_rel)) {
            main_bits = bits;
            snprintf(main_names, sizeof main_names, "%s", names);
        }
        if (!(bits & A_X86_64)) {
            if (n_find == cap_find) {
                cap_find = cap_find ? cap_find * 2 : 16;
                find = realloc(find, cap_find * sizeof *find);
            }
            find[n_find].rel = strdup(rel);
            snprintf(find[n_find++].archs, sizeof find->archs, "%s", names);
        }
    }
    fts_close(fts);
    int done = !(settle > 0 && now - newest < settle);
    if (done) {
        int universal = (main_bits & (A_X86_64 | A_ARM64)) == (A_X86_64 | A_ARM64);
        const char *kind = universal ? "universal" : main_bits & A_X86_64 ? "x86_64" : main_bits & A_ARM64 ? "arm64"
                         : "none";
        emit(universal && n_find, "app=%s version=%s main=%s main_archs=%s macho=%zu missing_x86_64=%zu", bundle,
             version[0] ? version : "-", kind, main_names, machos, universal ? n_find : 0);
        for (size_t i = 0; universal && i < n_find; i++)
            emit(1, "app=%s missing_x86_64=%s archs=%s", bundle, find[i].rel, find[i].archs);
    }
    for (size_t i = 0; i < n_find; i++)
        free(find[i].rel);
    free(find);
    return done;
}

static int is_dir(const char *p)
{
    struct stat st;
    return stat(p, &st) == 0 && S_ISDIR(st.st_mode);
}

static int mkdir_parent(const char *path)
{
    char dir[PATH_MAX];
    snprintf(dir, sizeof dir, "%s", path);
    char *slash = strrchr(dir, '/');
    if (!slash || slash == dir)
        return 0;
    *slash = 0;
    if (is_dir(dir))
        return 0;
    if (mkdir_parent(dir))
        return -1;
    return mkdir(dir, 0755) && errno != EEXIST ? -1 : 0;
}

/* returns the number of bundles left for a later run */
static int pass(char **roots, int nroots, int all, int settle, int *changed)
{
    int pending = 0;
    for (int r = 0; r < nroots; r++) {
        FTS *fts = NULL;
        char *paths[] = {roots[r], NULL};
        if (!(fts = fts_open(paths, FTS_PHYSICAL | FTS_NOCHDIR, NULL)))
            continue;
        FTSENT *e;
        while ((e = fts_read(fts))) {
            if (e->fts_info != FTS_D || e->fts_level != 1)
                continue;
            size_t l = e->fts_namelen;
            fts_set(fts, e, FTS_SKIP);
            if (l < 5 || strcmp(e->fts_name + l - 4, ".app"))
                continue;
            const char *bundle = e->fts_path;
            char receipt[PATH_MAX], info_path[PATH_MAX], exe[PATH_MAX], main_rel[PATH_MAX];
            snprintf(receipt, sizeof receipt, "%s/Contents/_MASReceipt/receipt", bundle);
            if (!all && access(receipt, F_OK))
                continue;
            snprintf(info_path, sizeof info_path, "%s/Contents/Info.plist", bundle);
            CFDictionaryRef info = read_info(bundle);
            char name[256], version[192], build[64];
            plist_string(info, "CFBundleExecutable", name, sizeof name);
            plist_string(info, "CFBundleShortVersionString", version, 128);
            plist_string(info, "CFBundleVersion", build, sizeof build);
            if (info)
                CFRelease(info);
            if (!name[0] || strchr(name, '/'))
                snprintf(name, sizeof name, "%.*s", (int)(l - 4), e->fts_name);
            if (build[0]) {   /* "1.2(345)" */
                size_t vl = strlen(version);
                snprintf(version + vl, sizeof version - vl, "(%s)", build);
            }
            one_word(version);
            snprintf(main_rel, sizeof main_rel, "Contents/MacOS/%s", name);
            snprintf(exe, sizeof exe, "%s/%s", bundle, main_rel);
            char key[256] = "";
            stat_key(receipt, key, sizeof key);
            stat_key(info_path, key, sizeof key);
            stat_key(exe, key, sizeof key);
            const char *old = state_get(bundle);
            if (old && !strcmp(old, key))
                continue;
            if (scan(bundle, main_rel, version, settle)) {
                state_set(bundle, key);
                *changed = 1;
            } else {
                pending++;
            }
        }
        fts_close(fts);
    }
    return pending;
}

int main(int argc, char **argv)
{
    int all = 0, settle = 30, wait = 120, i;
    for (i = 1; i < argc && argv[i][0] == '-'; i++) {
        if (!strcmp(argv[i], "--all"))
            all = 1;
        else if (!strcmp(argv[i], "--state") && i + 1 < argc)
            state_path = argv[++i];
        else if (!strcmp(argv[i], "--log") && i + 1 < argc)
            log_path = argv[++i];
        else if (!strcmp(argv[i], "--settle") && i + 1 < argc)
            settle = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--wait") && i + 1 < argc)
            wait = atoi(argv[++i]);
        else {
            fprintf(stderr, "usage: m27-slice-guard [--all] [--state FILE] [--log FILE] [--settle S] [--wait S] "
                            "[ROOT...]\n");
            return 2;
        }
    }
    char *default_root[] = {"/Applications"};
    char **roots = i < argc ? argv + i : default_root;
    int nroots = i < argc ? argc - i : 1;
    oslog = os_log_create("org.m27.slice-guard", "scan");
    if (mkdir_parent(log_path) == 0)
        log_file = fopen(log_path, "a");
    mkdir_parent(state_path);
    state_load();
    int changed = 0, pending;
    time_t start = time(NULL);
    while ((pending = pass(roots, nroots, all, settle, &changed)) && time(NULL) - start < wait)
        sleep(settle < 10 ? (settle > 0 ? (unsigned)settle : 1) : 10);
    if (pending)
        emit(0, "pending=%d (still being written; next run)", pending);
    if (changed)
        state_save();
    return 0;
}
