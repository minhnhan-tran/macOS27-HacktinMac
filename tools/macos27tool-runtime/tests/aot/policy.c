/*
 * Unit test of the AOT trust policy and the cache format checks
 * (src/aot_policy.c, linked from libm27core.a):
 *   policy TMPDIR
 * TMPDIR must be a directory this user owns, with trusted ancestors (the
 * scratch directory). Prints one PASS/FAIL line per check; exit 1 on any FAIL.
 */
#include "m27/aot.h"
#include "m27/bridge.h"
#include "m27/cpufeat.h"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/acl.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int fails, passes;
#define CHECK(cond, ...) do { if (cond) passes++; else { fails++; printf("FAIL "); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* ------------------------------------------------------------ tiers */
static void test_tiers(void)
{
    m27_proc_facts f = {.pid = 500, .ppid = 400, .uid = 501, .euid = 501, .gid = 20, .egid = 20};
    CHECK(m27_env_tier_for(&f) == M27_ENV_OPEN, "ordinary user process is OPEN");
    m27_proc_facts g = f; g.setugid = true;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "issetugid() is SETUGID");
    g = f; g.euid = 0;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "uid != euid (setuid root) is SETUGID");
    g = f; g.egid = 12;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "gid != egid (setgid) is SETUGID");
    g = f; g.pid = 1; g.ppid = 0; g.uid = g.euid = 0; g.gid = g.egid = 0;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "PID 1 is GUARDED");
    g = f; g.ppid = 1; g.uid = g.euid = 0;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "root daemon (ppid 1) is GUARDED");
    g = f; g.ppid = 1;
    CHECK(m27_env_tier_for(&g) == M27_ENV_OPEN, "user agent (ppid 1, uid 501) is OPEN");
    g = f; g.identity = true;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "a listed Apple program (stub-identities) run by a user is GUARDED");
    g.setugid = true;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "a listed set-ID program stays SETUGID");
    g = f; g.uid = g.euid = 0;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "root shell (ppid != 1) is GUARDED");
    g = f; g.uid = g.euid = 0; g.gid = g.egid = 0; g.ppid = 77;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "root under sudo or cron (any parent) is GUARDED");
    g = f; g.uid = 0; g.euid = 501;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "root that dropped only its effective uid is SETUGID");
    g = f; g.restricted = true;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "dyld-restricted is GUARDED");
    g = f; g.entitled = true;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "entitled is GUARDED");
    g = f; g.distrust = 1;
    CHECK(m27_env_tier_for(&g) == M27_ENV_GUARDED, "M27_DISTRUST_ENV=1 is GUARDED");
    g = f; g.distrust = 7;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "M27_DISTRUST_ENV=7 is SETUGID");
    g = f; g.setugid = true; g.distrust = -5;
    CHECK(m27_env_tier_for(&g) == M27_ENV_SETUGID, "M27_DISTRUST_ENV cannot raise trust");

    /* code, paths, checks, bridging, signal and memory behaviour: an ordinary user's process only */
    static const char *open_only[] = {"M27_CACHE", "M27_GUEST_ROOT", "M27_FLAGS", "M27_AOT_LEARN", "M27_NO_MSGSEND",
                                      "M27_NO_CLOSURES", "M27_NO_SWIFTREG", "M27_NO_BLOCK_THUNKS", "M27_SOMETHING_NEW",
                                      "M27_IF_DIR", "M27_DIAG_DIR", "M27_TRUST_LIST", "M27_ASYNC_CONTRACT_LIBRARY",
                                      "M27_NO_LIBVAL", "M27_NO_VARSNIFF", "M27_NO_IFUNITS", "M27_NO_THUNKS",
                                      "M27_NATIVE_LIBCXX", "M27_NO_FIXUP_THUNKS", "M27_NO_STRUCT_THUNKS",
                                      "M27_NO_SWIFT_THUNKS", "M27_NO_SWIFT_VALUES", "M27_SWIFT_POP", "M27_BRIDGE_CHECK",
                                      "M27_SIGRETURN_KERNEL", "M27_NO_PROBE", "M27_WATCHDOG", "M27_JIT_CACHE_MB",
                                      "M27_SWIFT_VALUE_REPORT", NULL};
    /* the same code by another engine, and printing: guarded too, never set-ID */
    static const char *guarded[] = {"M27_NO_AOT", "M27_INTERP", "M27_NO_CHAIN", "M27_JIT_HELPERS_ONLY", "M27_VERBOSE",
                                    "M27_CRASH_TRACE", "M27_NX_STATS", "M27_BRIDGE_STATS", "M27_DIAG_TIME", "M27_AOT_USE", NULL};
    static const char *test_only[] = {"M27_LIBVAL_USER", "M27_SIGRETURN_USER", "M27_AOT_LIMIT_KB", "M27_BRIDGE_ABORT",
                                      "M27_NO_JIT_MEMORY", "M27_CODE_ALIAS", NULL};
    for (const char **s = open_only; *s; s++) {
        CHECK(m27_env_switch_allowed(*s, M27_ENV_OPEN), "%s allowed when OPEN", *s);
        CHECK(!m27_env_switch_allowed(*s, M27_ENV_GUARDED), "%s ignored when GUARDED", *s);
        CHECK(!m27_env_switch_allowed(*s, M27_ENV_SETUGID), "%s ignored when SETUGID", *s);
        CHECK(!m27_env_test_switch(*s), "%s is not a test switch", *s);
    }
    for (const char **s = guarded; *s; s++) {
        CHECK(m27_env_switch_allowed(*s, M27_ENV_OPEN), "%s allowed when OPEN", *s);
        CHECK(m27_env_switch_allowed(*s, M27_ENV_GUARDED), "%s allowed when GUARDED", *s);
        CHECK(!m27_env_switch_allowed(*s, M27_ENV_SETUGID), "%s ignored when SETUGID", *s);
    }
    for (const char **s = test_only; *s; s++) {
        CHECK(m27_env_test_switch(*s), "%s is a test switch", *s);
        CHECK(m27_env_switch_allowed(*s, M27_ENV_OPEN) == m27_test_switches, "%s when OPEN: read by a M27_TEST_SWITCHES build only (%d)",
              *s, m27_test_switches);
        CHECK(!m27_env_switch_allowed(*s, M27_ENV_GUARDED), "%s ignored when GUARDED", *s);
        CHECK(!m27_env_switch_allowed(*s, M27_ENV_SETUGID), "%s ignored when SETUGID", *s);
    }

    /* dependency paths: dyld's rule for a set-ID main executable, nothing refused elsewhere */
    static const struct { const char *path; bool rpath, mainimg, setugid_ok; const char *why; } dp[] = {
        {"/usr/lib/libz.1.dylib", false, true, true, NULL},
        {"/opt/x/lib", true, true, true, NULL},
        {"@rpath/libfoo.dylib", false, true, true, NULL},
        {"@executable_path/libfoo.dylib", false, true, false, "@ path"},
        {"@executable_path/libfoo.dylib", false, false, false, "@ path"},
        {"@executable_path/../lib", true, true, false, "@ path"},
        {"@executable_path", true, false, false, "@ path"},
        {"@loader_path/libfoo.dylib", false, true, true, NULL},
        {"@loader_path/libfoo.dylib", false, false, true, NULL},
        {"@loader_path/../lib", true, false, true, NULL},
        {"@loader_path/../lib", true, true, false, "@ path"},
        {"@loader_path", true, true, false, "@ path"},
        {"libfoo.dylib", false, true, false, "relative path"},
        {"./libfoo.dylib", false, false, false, "relative path"},
        {"../lib/libfoo.dylib", false, true, false, "relative path"},
        {"lib", true, true, false, "relative path"},
        {"../lib", true, false, false, "relative path"},
        {"@executable_pathology/x", false, true, true, NULL},   /* not the variable: expands to nothing anyway */
    };
    for (size_t i = 0; i < sizeof dp / sizeof dp[0]; i++) {
        const char *why = "unset";
        CHECK(m27_dep_path_allowed(dp[i].path, dp[i].rpath, dp[i].mainimg, M27_ENV_OPEN, &why) && !why,
              "dep %s allowed when OPEN", dp[i].path);
        CHECK(m27_dep_path_allowed(dp[i].path, dp[i].rpath, dp[i].mainimg, M27_ENV_GUARDED, NULL), "dep %s allowed when GUARDED",
              dp[i].path);
        bool ok = m27_dep_path_allowed(dp[i].path, dp[i].rpath, dp[i].mainimg, M27_ENV_SETUGID, &why);
        CHECK(ok == dp[i].setugid_ok && (ok ? !why : why && strstr(why, dp[i].why)),
              "dep %s (%s of %s) when SETUGID: %s (%s)", dp[i].path, dp[i].rpath ? "LC_RPATH" : "install name",
              dp[i].mainimg ? "the main executable" : "a library", ok ? "allowed" : "refused", why ? why : "-");
    }

    const char *own = "<plist><dict><key>com.apple.security.cs.allow-jit</key><true/>"
                      "<key>com.apple.security.cs.disable-library-validation</key><true/></dict></plist>";
    const char *more = "<plist><dict><key>com.apple.security.cs.allow-jit</key><true/>"
                       "<key>com.apple.private.tcc.allow</key><array/></dict></plist>";
    CHECK(!m27_entitlements_foreign(own, strlen(own)), "m27's own entitlements are not foreign");
    CHECK(m27_entitlements_foreign(more, strlen(more)), "a private entitlement is foreign");
    CHECK(m27_entitlements_foreign("<key>com.apple", 14), "malformed entitlements count as foreign");
    CHECK(!m27_entitlements_foreign("", 0), "no entitlements are not foreign");
    static const char cow[] = "com.apple.security.hardened-process.containment.vm.cow-defeatured";
    static const char on1[] = "<dict><key>com.apple.security.hardened-process.containment.vm.cow-defeatured</key>\n\t<integer>1</integer></dict>";
    static const char ont[] = "<key>com.apple.security.hardened-process.containment.vm.cow-defeatured</key><true/>";
    static const char off0[] = "<key>com.apple.security.hardened-process.containment.vm.cow-defeatured</key><integer>0</integer>";
    static const char offf[] = "<key>com.apple.security.hardened-process.containment.vm.cow-defeatured</key> <false/>";
    static const char other[] = "<key>com.apple.security.hardened-process.containment.vm.cow-defeatured.x</key><true/>"
                                "<key>com.apple.security.cs.allow-jit</key><true/>";
    CHECK(m27_entitlement_on(on1, strlen(on1), cow), "an integer 1 entitlement is on");
    CHECK(m27_entitlement_on(ont, strlen(ont), cow), "a true entitlement is on");
    CHECK(!m27_entitlement_on(off0, strlen(off0), cow), "an integer 0 entitlement is off");
    CHECK(!m27_entitlement_on(offf, strlen(offf), cow), "a false entitlement is off");
    CHECK(!m27_entitlement_on(other, strlen(other), cow), "a longer key is another entitlement");
    CHECK(!m27_entitlement_on("", 0, cow), "no entitlements: off");
}

/* -------------------------------------------------------- locations */
static bool bad_prefix(const char *p)
{
    return !strncmp(p, "/tmp", 4) || !strncmp(p, "/private/tmp", 12) || !strncmp(p, "/Library", 8) || !strncmp(p, "/var/tmp", 8);
}

static void test_dirs(void)
{
    m27_aot_dir d[M27_AOT_MAX_DIRS];
    int w, k;
    char why[256];
    struct { int tier; uid_t euid; const char *pw, *cache; int n; const char *dirs[3]; const char *wr; const char *what; } c[] = {
        {M27_ENV_GUARDED, 0, NULL, NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, M27_AOT_SYS_DIR, "PID 1 / root daemon, no HOME"},
        {M27_ENV_GUARDED, 0, NULL, "/tmp/evil", 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, M27_AOT_SYS_DIR, "root daemon ignores M27_CACHE"},
        {M27_ENV_SETUGID, 0, "/Users/x", "/Users/x/evil", 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "setuid root: system only, no write"},
        {M27_ENV_SETUGID, 501, "/Users/x", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "setgid user: no user dir"},
        {M27_ENV_GUARDED, 501, "/Users/x", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "entitled user process: no user dir"},
        {M27_ENV_GUARDED, 0, "/var/root", "/var/root/c", 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, M27_AOT_SYS_DIR,
         "root shell: M27_CACHE and its home ignored"},
        {M27_ENV_OPEN, 501, NULL, NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "user without a home: no write"},
        {M27_ENV_OPEN, 501, "/Users/x", NULL, 3, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR, "/Users/x/Library/Caches/m27"},
         "/Users/x/Library/Caches/m27", "ordinary user"},
        {M27_ENV_OPEN, 501, "/Users/x/", NULL, 3, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR, "/Users/x/Library/Caches/m27"},
         "/Users/x/Library/Caches/m27", "home with trailing slash"},
        {M27_ENV_OPEN, 501, "", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "home \"\""},
        {M27_ENV_OPEN, 501, "/", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "home \"/\""},
        {M27_ENV_OPEN, 501, "relative", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "relative home"},
        {M27_ENV_OPEN, 4294967294u, "/var/empty", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "_nobody"},
        {M27_ENV_OPEN, 200, "/var/db/foo", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "system account uid 200"},
        {M27_ENV_OPEN, 501, "/var/empty", NULL, 2, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR}, NULL, "home /var/empty"},
        {M27_ENV_OPEN, 501, "/Users/x", "/Users/x/c", 1, {"/Users/x/c"}, "/Users/x/c", "M27_CACHE is exclusive"},
        {M27_ENV_OPEN, 501, "/Users/x", "rel/c", 0, {NULL}, NULL, "relative M27_CACHE: AOT off"},
        {M27_ENV_OPEN, 501, "/Users/x", "", 3, {M27_AOT_SYS_RO_DIR, M27_AOT_SYS_DIR, "/Users/x/Library/Caches/m27"},
         "/Users/x/Library/Caches/m27", "empty M27_CACHE is unset"},
    };
    for (size_t i = 0; i < sizeof c / sizeof c[0]; i++) {
        int n = m27_aot_dirs_for(c[i].tier, c[i].euid, c[i].pw, c[i].cache, d, &w, why, sizeof why);
        bool ok = n == c[i].n;
        for (k = 0; ok && k < n; k++) ok = !strcmp(d[k].path, c[i].dirs[k]) && (c[i].cache && c[i].cache[0] ? true : !bad_prefix(d[k].path));
        ok = ok && (c[i].wr ? w >= 0 && !strcmp(d[w].path, c[i].wr) : w < 0);
        CHECK(ok, "dirs: %s (got %d dirs%s%s, write %s)", c[i].what, n, n ? ", first " : "", n ? d[0].path : "", w >= 0 ? d[w].path : "none");
    }
    m27_aot_dirs_for(M27_ENV_OPEN, 501, "/Users/x", "rel/c", d, &w, why, sizeof why);
    CHECK(strstr(why, "not absolute") != NULL, "relative M27_CACHE says why (%s)", why);

    /* interface units (m27/bridge.h): M27_IF_DIR replaces the system directory, OPEN only */
    static const struct { int tier; const char *dir; int n, kind; const char *path, *what; } u[] = {
        {M27_ENV_OPEN, NULL, 1, M27_AOT_SYS_RO, M27_IF_SYS_DIR, "units: system directory"},
        {M27_ENV_OPEN, "", 1, M27_AOT_SYS_RO, M27_IF_SYS_DIR, "units: empty M27_IF_DIR is unset"},
        {M27_ENV_OPEN, "/Users/x/if", 1, M27_AOT_EXPLICIT, "/Users/x/if", "units: M27_IF_DIR is exclusive"},
        {M27_ENV_OPEN, "rel/if", 0, 0, NULL, "units: relative M27_IF_DIR: no units"},
        {M27_ENV_GUARDED, "/Users/x/if", 1, M27_AOT_SYS_RO, M27_IF_SYS_DIR, "units: M27_IF_DIR ignored when GUARDED"},
        {M27_ENV_SETUGID, "/Users/x/if", 1, M27_AOT_SYS_RO, M27_IF_SYS_DIR, "units: M27_IF_DIR ignored when SETUGID"},
    };
    for (size_t i = 0; i < sizeof u / sizeof u[0]; i++) {
        int n = m27_if_dirs_for(u[i].tier, u[i].dir, d, why, sizeof why);
        CHECK(n == u[i].n && (!n || (d[0].kind == u[i].kind && !strcmp(d[0].path, u[i].path))), "%s (got %d%s%s)", u[i].what, n,
              n ? ", " : "", n ? d[0].path : "");
    }
    m27_if_dirs_for(M27_ENV_OPEN, "rel/if", d, why, sizeof why);
    CHECK(strstr(why, "not absolute") != NULL, "relative M27_IF_DIR says why (%s)", why);
}

/* -------------------------------------------------- owner/mode rules */
static struct stat mk(mode_t type, mode_t perm, uid_t uid)
{
    struct stat st;
    memset(&st, 0, sizeof st);
    st.st_mode = type | perm;
    st.st_uid = uid;
    return st;
}

static void test_stat_rules(const char *tmp)
{
    char why[256];
    uid_t me = geteuid();
    struct stat st = mk(S_IFREG, 0644, 0);
    CHECK(m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_FILE, me, why, sizeof why), "root 0644 file ok in SYS");
    st = mk(S_IFREG, 0644, me);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_FILE, me, why, sizeof why), "user-owned file rejected in SYS");
    CHECK(m27_aot_stat_ok(&st, M27_AOT_USER, M27_AOT_FILE, me, why, sizeof why), "user-owned file ok in USER");
    st = mk(S_IFREG, 0644, me + 1);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_EXPLICIT, M27_AOT_FILE, me, why, sizeof why), "other user's file rejected");
    st = mk(S_IFREG, 0664, 0);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_FILE, me, why, sizeof why), "group-writable file rejected");
    st = mk(S_IFREG, 0646, 0);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_FILE, me, why, sizeof why), "world-writable file rejected");
    st = mk(S_IFLNK, 0755, 0);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_FILE, me, why, sizeof why), "symlink file rejected");
    st = mk(S_IFIFO, 0644, 0);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_FILE, me, why, sizeof why), "FIFO rejected");
    st = mk(S_IFDIR, 01777, 0);
    CHECK(m27_aot_stat_ok(&st, M27_AOT_USER, M27_AOT_ANCESTOR, me, why, sizeof why), "sticky 1777 ancestor ok for USER");
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_SYS, M27_AOT_ANCESTOR, me, why, sizeof why), "sticky ancestor rejected for SYS");
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_USER, M27_AOT_DIR, me, why, sizeof why), "sticky 1777 cache dir rejected");
    st = mk(S_IFDIR, 0775, 0);
    CHECK(!m27_aot_stat_ok(&st, M27_AOT_USER, M27_AOT_ANCESTOR, me, why, sizeof why), "0775 ancestor rejected");

    /* real directories */
    char p[1024], q[1024], r[1024];
    snprintf(p, sizeof p, "%s/d755", tmp); mkdir(p, 0755); chmod(p, 0755);
    CHECK(m27_aot_dir_ok(p, M27_AOT_EXPLICIT, me, why, sizeof why), "0755 dir ok (%s)", why);
    CHECK(!m27_aot_dir_ok(p, M27_AOT_SYS, me, why, sizeof why), "user-owned dir rejected for SYS");
    snprintf(p, sizeof p, "%s/d775", tmp); mkdir(p, 0775); chmod(p, 0775);
    CHECK(!m27_aot_dir_ok(p, M27_AOT_EXPLICIT, me, why, sizeof why), "0775 dir rejected");
    snprintf(p, sizeof p, "%s/d1777", tmp); mkdir(p, 01777); chmod(p, 01777);
    CHECK(!m27_aot_dir_ok(p, M27_AOT_EXPLICIT, me, why, sizeof why), "1777 cache dir rejected");
    snprintf(q, sizeof q, "%s/d1777/c", tmp); mkdir(q, 0700); chmod(q, 0700);
    CHECK(m27_aot_dir_ok(q, M27_AOT_EXPLICIT, me, why, sizeof why), "0700 dir under a sticky 1777 dir ok (%s)", why);
    snprintf(p, sizeof p, "%s/d775/c", tmp); mkdir(p, 0700); chmod(p, 0700);
    CHECK(!m27_aot_dir_ok(p, M27_AOT_EXPLICIT, me, why, sizeof why), "0700 dir under a 0775 dir rejected");
    snprintf(p, sizeof p, "%s/d755", tmp);
    snprintf(r, sizeof r, "%s/link", tmp); unlink(r); symlink(p, r);
    CHECK(!m27_aot_dir_ok(r, M27_AOT_EXPLICIT, me, why, sizeof why), "symlinked cache dir rejected");
    CHECK(!m27_aot_literal_ok(r, why, sizeof why), "user-owned symlink in the configured path rejected");
    CHECK(m27_aot_literal_ok("/tmp/nonexistent-m27-test/c", why, sizeof why), "root-owned /tmp symlink in the configured path ok");
    CHECK(!m27_aot_dir_ok("/private/tmp", M27_AOT_EXPLICIT, me, why, sizeof why), "/private/tmp itself rejected");
    CHECK(!m27_aot_dir_ok("/Library/Caches", M27_AOT_EXPLICIT, me, why, sizeof why), "/Library/Caches rejected");
    CHECK(!m27_aot_dir_ok("relative", M27_AOT_EXPLICIT, me, why, sizeof why), "relative path rejected");
}

/* A program that requires library validation reads AOT caches from the system directories only: neither
 * M27_CACHE nor the user's own directory, which the user can write (guest_sig.c sets the flag). */
static void test_system_only(void)
{
    m27_aot_dir d[M27_AOT_MAX_DIRS];
    int w;
    char why[256];
    m27_aot_system_only = true;
    int n = m27_aot_dirs_for(M27_ENV_OPEN, 501, "/Users/x", NULL, d, &w, why, sizeof why);
    CHECK(n == 2 && !strcmp(d[0].path, M27_AOT_SYS_RO_DIR) && !strcmp(d[1].path, M27_AOT_SYS_DIR) && w == -1,
          "system only: an ordinary user's process reads the system directories, writes none (%d dirs)", n);
    n = m27_aot_dirs_for(M27_ENV_OPEN, 501, "/Users/x", "/Users/x/c", d, &w, why, sizeof why);
    CHECK(n == 2 && !strcmp(d[1].path, M27_AOT_SYS_DIR) && w == -1, "system only: M27_CACHE not used (%d dirs)", n);
    m27_aot_system_only = false;
    n = m27_aot_dirs_for(M27_ENV_OPEN, 501, "/Users/x", NULL, d, &w, why, sizeof why);
    CHECK(n == 3, "without it: the user's directory is read again (%d dirs)", n);
}

/* ACLs: only "no extended ACL" (ENOENT from the lookup) grants nothing. A lookup that fails otherwise
 * leaves the entries unknown, and counts as granting write access. */
static void test_acl(const char *tmp)
{
    char d[1024], f[1024], cmd[2200];
    CHECK(!m27_aot_acl_allows(NULL, ENOENT), "ACL lookup said ENOENT (no ACL): grants nothing");
    CHECK(m27_aot_acl_allows(NULL, EACCES) && m27_aot_acl_allows(NULL, EIO) && m27_aot_acl_allows(NULL, ENOMEM) &&
          m27_aot_acl_allows(NULL, 0), "ACL lookup failed otherwise (EACCES, EIO, ENOMEM, no errno): counts as granting");
    snprintf(d, sizeof d, "%s/acl", tmp);
    mkdir(d, 0700);
    chmod(d, 0700);
    snprintf(f, sizeof f, "%s/file", d);
    close(open(f, O_WRONLY | O_CREAT, 0644));
    errno = 0;
    acl_t a = acl_get_link_np(f, ACL_TYPE_EXTENDED);
    bool r = m27_aot_acl_allows(a, errno);
    if (a) acl_free(a);
    CHECK(!a && !r, "a file without an ACL: grants nothing");
    /* the lookup itself fails: the directory above denies search */
    chmod(d, 0);
    errno = 0;
    a = acl_get_link_np(f, ACL_TYPE_EXTENDED);
    int e = errno;
    r = m27_aot_acl_allows(a, e);
    if (a) acl_free(a);
    chmod(d, 0700);
    CHECK(!a && e != ENOENT && r, "an ACL that cannot be read (errno %d): counts as granting", e);
    snprintf(cmd, sizeof cmd, "/bin/chmod +a 'everyone allow write' '%s'", f);
    if (system(cmd) == 0) {
        errno = 0;
        a = acl_get_link_np(f, ACL_TYPE_EXTENDED);
        r = m27_aot_acl_allows(a, errno);
        if (a) acl_free(a);
        CHECK(a && r, "an ACL granting everyone write: grants");
    }
    unlink(f);
    rmdir(d);
}

/* ------------------------------------------------------ format checks */
static uint8_t gen[16] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
static uint8_t uuid[16] = {0xaa, 0xbb, 0xcc};
static uint64_t rlo[1] = {0x4000}, rhi[1] = {0x8000};
/* the guest's code identity: a cdhash (20 bytes, the rest zero) of hash type 2 (SHA-256) */
static uint8_t cdhash[M27_AOT_ID_LEN] = {0xc0, 0xde, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18};

/* A valid file: 3 blocks at code 16/32/48, one link from block 0 to block 2. */
static size_t build(uint8_t *f, m27_aot_expect *x)
{
    m27_aot_hdr h;
    memset(&h, 0, sizeof h);
    memcpy(h.magic, M27_AOT_MAGIC, 8);
    h.version = M27_AOT_VERSION;
    h.hdr_size = sizeof h;
    memcpy(h.uuid, uuid, 16);
    memcpy(h.gen, gen, 16);
    h.vmsize = 0x10000;
    h.guest_id = 0x1234;
    h.id_kind = M27_AOT_ID_CDHASH;
    h.id_hash = 2;
    memcpy(h.code_id, cdhash, sizeof h.code_id);
    h.nblocks = 3;
    h.nlinks = 1;
    h.code_size = 64;
    m27_aot_ent e[3] = {{0x4000, 16}, {0x4010, 32}, {0x4100, 48}};
    m27_aot_link l = {20, 2};
    uint8_t code[64];
    memset(code, 0x90, sizeof code);
    int32_t rel = 48 - (20 + 4);
    memcpy(code + 20, &rel, 4);
    h.file_size = sizeof h + sizeof e + sizeof l + sizeof code;
    uint8_t *q = f + sizeof h;
    memcpy(q, e, sizeof e); q += sizeof e;
    memcpy(q, &l, sizeof l); q += sizeof l;
    memcpy(q, code, sizeof code);
    memcpy(f, &h, sizeof h);
    h.payload_hash = m27_aot_file_hash(f, (size_t)h.file_size);
    memcpy(f, &h, sizeof h);
    *x = (m27_aot_expect){uuid, gen, 0x10000, 0x1234, rlo, rhi, 1, M27_AOT_ID_CDHASH, 2, cdhash};
    return (size_t)h.file_size;
}

static void rehash(uint8_t *f, size_t n)
{
    m27_aot_hdr *h = (m27_aot_hdr *)f;
    h->payload_hash = m27_aot_file_hash(f, n);
}

/* After an accepted check, verify independently that the loader would only
 * touch memory inside the file and the image. */
static bool view_in_bounds(const uint8_t *f, size_t n, const m27_aot_view *v, const m27_aot_expect *x)
{
    const m27_aot_hdr *h = v->hdr;
    if ((const uint8_t *)v->code < f || (const uint8_t *)v->code + h->code_size != f + n) return false;
    for (uint32_t k = 0; k < h->nblocks; k++)
        if (v->ents[k].code_off >= h->code_size || v->ents[k].guest_off >= x->vmsize || (v->ents[k].code_off & 15)) return false;
    return true;
}

static void test_format(void)
{
    static uint64_t store[1024];
    uint8_t *f = (uint8_t *)store;
    m27_aot_expect x;
    m27_aot_view v;
    char why[256];
    size_t n = build(f, &x);
    CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == 0, "valid cache accepted (%s)", why);
    m27_aot_hdr *h = (m27_aot_hdr *)f;
    m27_aot_ent *e = (m27_aot_ent *)(f + sizeof *h);
    m27_aot_link *l = (m27_aot_link *)(f + sizeof *h + 3 * sizeof *e);
#define MUT(what, stmt, hash, needle) do { n = build(f, &x); stmt; if (hash) rehash(f, n); \
        int r_ = m27_aot_check(f, n, &x, &v, why, sizeof why); \
        CHECK(r_ != 0 && strstr(why, needle), "%s rejected (%s)", what, r_ ? why : "accepted"); } while (0)
    MUT("bad magic", f[0] ^= 1, 0, "not a m27");
    MUT("version 4", h->version = 4, 0, "format version");
    MUT("version 6", h->version = 6, 0, "format version");
    MUT("version 7 (no CPU profile)", h->version = 7, 0, "format version");
    MUT("header size", h->hdr_size = 64, 0, "header size");
    MUT("other guest uuid", h->uuid[3] ^= 1, 0, "UUID");
    MUT("header from another m27 build", h->gen[15] ^= 1, 0, "another m27 build");
    MUT("vmsize", h->vmsize += 0x1000, 0, "vmsize");
    MUT("guest load commands", h->guest_id ^= 1, 0, "load commands");
    /* the code identity: a cache that names this guest in every other field
     * (UUID, vmsize, load commands, build) but was translated from other code */
    MUT("other code: one cdhash bit", h->code_id[0] ^= 1, 0, "guest code differs");
    MUT("other code: last cdhash byte", h->code_id[M27_AOT_CDHASH_LEN - 1] ^= 0x80, 0, "guest code differs");
    MUT("other code: byte past the cdhash", h->code_id[M27_AOT_ID_LEN - 1] = 1, 0, "guest code differs");
    MUT("other code: zero identity", memset(h->code_id, 0, sizeof h->code_id), 0, "guest code differs");
    MUT("other code, payload hash fixed", h->code_id[5] ^= 0x10, 1, "guest code differs");
    MUT("same cdhash bytes, another hash type", h->id_hash = 1, 0, "guest code differs");
    MUT("cache of an unsigned twin for a signed image", h->id_kind = M27_AOT_ID_CODE, 0, "unsigned guest");
    MUT("cache of a signed twin for an unsigned image", x.id_kind = M27_AOT_ID_CODE, 0, "code-signed guest");
    MUT("identity kind 0", h->id_kind = 0, 0, "unknown code identity kind");
    MUT("identity kind 3", h->id_kind = 3, 0, "unknown code identity kind");
    MUT("identity kind 0 on both sides", (h->id_kind = 0, x.id_kind = 0), 0, "no code identity");
    MUT("image whose identity kind is unknown", x.id_kind = 0, 0, "no code identity");
    {
        /* the reason names both identities */
        n = build(f, &x);
        h->code_id[0] ^= 1;
        m27_aot_check(f, n, &x, &v, why, sizeof why);
        CHECK(strstr(why, "c0de0102030405060708090a0b0c0d0e0f101112") && strstr(why, "c1de0102030405060708090a0b0c0d0e0f101112"),
              "a code mismatch names the image's cdhash and the cache's (%s)", why);
        /* identity not computed yet: never accepted, and asked for only by a
         * file that names this guest, this build and this kind of identity */
        n = build(f, &x);
        x.code_id = NULL;
        CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == M27_AOT_NEED_ID, "no identity yet: a matching header asks for it");
        h->code_id[0] ^= 1;
        CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == M27_AOT_NEED_ID, "no identity yet: never accepted, whatever the file says");
        memset(h->code_id, 0, sizeof h->code_id);
        CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == M27_AOT_NEED_ID, "no identity yet: a zero identity in the file is not accepted");
        n = build(f, &x); x.code_id = NULL; h->gen[0] ^= 1;
        CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == -1 && strstr(why, "another m27 build"),
              "no identity yet: another build's cache is rejected without asking");
        n = build(f, &x); x.code_id = NULL; h->id_kind = M27_AOT_ID_CODE;
        CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == -1 && strstr(why, "unsigned guest"),
              "no identity yet: a cache of the other kind is rejected without asking");
        n = build(f, &x); x.code_id = NULL; h->guest_id ^= 1;
        CHECK(m27_aot_check(f, n, &x, &v, why, sizeof why) == -1, "no identity yet: other load commands are rejected without asking");
        n = build(f, &x);
    }
    MUT("file_size field", h->file_size += 8, 0, "header says");
    MUT("truncated file", n -= 8, 0, "header says");
    MUT("truncated to the header", n = 40, 0, "truncated");
    MUT("code_size + 65536 wrap", h->code_size = 0xFFFFFFFFFFFF0000ull, 0, "do not add up");
    MUT("code_size larger", h->code_size += 16, 0, "do not add up");
    MUT("nblocks huge", h->nblocks = 0xffffffffu, 0, "blocks for");
    MUT("nblocks + 1", h->nblocks += 1, 0, "do not add up");
    MUT("nlinks huge", h->nlinks = 0x40000000u, 0, "do not add up");
    MUT("payload byte flipped, hash not fixed", f[n - 1] ^= 1, 0, "hash");
    MUT("entry byte flipped, hash not fixed", e[0].code_off ^= 0x100, 0, "hash");
    /* the header is hashed too: a profile with a feature cleared would let code using it load where it cannot run */
    MUT("CPU profile bit cleared, hash not fixed", (h->cpu_profile = M27_CPU_AES | M27_CPU_PCLMUL, rehash(f, n), x.cpu = M27_CPU_AES,
        h->cpu_profile = M27_CPU_AES), 0, "hash");
    MUT("CPU profile bit set, hash not fixed", (h->cpu_profile |= M27_CPU_AES, x.cpu = M27_CPU_AES), 0, "hash");
    MUT("guest_id changed, hash not fixed", (x.guest_id ^= 1, h->guest_id ^= 1), 0, "hash");
    MUT("code_off 0x7ffffff0", e[1].code_off = 0x7ffffff0, 1, "outside the code area");
    MUT("code_off = code_size", e[2].code_off = 64, 1, "outside the code area");
    MUT("code_off in the base slot", e[0].code_off = 0, 1, "outside the code area");
    MUT("code_off misaligned", e[1].code_off = 33, 1, "not 16-aligned");
    MUT("code_off not increasing", e[2].code_off = 32, 1, "not increasing");
    MUT("guest_off 0xfffffff0", e[1].guest_off = 0xfffffff0u, 1, "outside the image");
    MUT("guest_off outside code ranges", e[1].guest_off = 0x100, 1, "outside the image");
    MUT("guest_off at the end of the code range", e[1].guest_off = 0x8000, 1, "outside the image");
    MUT("guest_off misaligned", e[1].guest_off = 0x4012, 1, "not 4-aligned");
    MUT("link target out of range", l->target = 3, 1, "target block");
    MUT("link site past the end", l->site = 62, 1, "outside the code area");
    MUT("link site in the base slot", l->site = 4, 1, "outside the code area");
    MUT("link rel32 changed", f[n - 64 + 20] ^= 4, 1, "does not reach");
    n = build(f, &x);
    CHECK(m27_aot_check(f + 4, n - 4, &x, &v, why, sizeof why) != 0, "unaligned buffer rejected");

    /* The CPU profile: a cache loads iff every feature it was translated for
     * is one the host has; the reason names exactly the missing ones. */
    static const struct { uint64_t profile, host; const char *missing; } cp[] = {
        {0, 0, NULL},
        {0, M27_CPU_ALL, NULL},
        {M27_CPU_AES, M27_CPU_AES | M27_CPU_PCLMUL, NULL},
        {M27_CPU_AES | M27_CPU_PCLMUL, M27_CPU_AES | M27_CPU_PCLMUL, NULL},
        {M27_CPU_BASELINE, M27_CPU_BASELINE | M27_CPU_AVX2 | M27_CPU_FMA | M27_CPU_BMI2, NULL},   /* baseline cache, newer Mac */
        {M27_CPU_AES | M27_CPU_PCLMUL, M27_CPU_AES, "pclmul"},
        {M27_CPU_PCLMUL, 0, "pclmul"},
        {M27_CPU_AES | M27_CPU_PCLMUL, 0, "aes,pclmul"},
        {M27_CPU_BASELINE | M27_CPU_AVX2 | M27_CPU_FMA, M27_CPU_BASELINE, "fma,avx2"},           /* newer Mac's cache, 2012 Mac */
        {M27_CPU_AVX512F | M27_CPU_AVX512VL, M27_CPU_ALL & ~M27_CPU_AVX512VL, "avx512vl"},
        {1ull << 40, M27_CPU_ALL, "bit40"},
    };
    for (unsigned k = 0; k < sizeof cp / sizeof *cp; k++) {
        n = build(f, &x);
        h->cpu_profile = cp[k].profile;
        rehash(f, n);
        x.cpu = cp[k].host;
        int r = m27_aot_check(f, n, &x, &v, why, sizeof why);
        if (!cp[k].missing) CHECK(r == 0, "profile %#llx on host %#llx accepted (%s)", (unsigned long long)cp[k].profile,
                                  (unsigned long long)cp[k].host, r ? why : "ok");
        else {
            const char *m = strstr(why, "lacks: ");
            CHECK(r == -1 && m && !strcmp(m + 7, cp[k].missing), "profile %#llx on host %#llx rejected naming %s (%s)",
                  (unsigned long long)cp[k].profile, (unsigned long long)cp[k].host, cp[k].missing, r ? why : "accepted");
        }
        x.code_id = NULL;   /* decided before the guest's identity is computed */
        r = m27_aot_check(f, n, &x, &v, why, sizeof why);
        CHECK(cp[k].missing ? r == -1 : r == M27_AOT_NEED_ID, "profile %#llx on host %#llx: no identity asked for a cache this host cannot run",
              (unsigned long long)cp[k].profile, (unsigned long long)cp[k].host);
    }
    n = build(f, &x);

    /* random mutations, rehashed, so they reach every check: never accept a
     * view that would leave the file or the image */
    srandom(12345);
    int accepted = 0, rejected = 0, bad = 0;
    for (int it = 0; it < 200000; it++) {
        n = build(f, &x);
        int nm = 1 + (int)(random() % 3);
        for (int m = 0; m < nm; m++) {
            size_t at = (size_t)random() % n;
            if (random() & 1) f[at] ^= (uint8_t)(1u << (random() % 8));
            else f[at] = (uint8_t)random();
        }
        if (random() % 8 == 0) n = (size_t)random() % (n + 1);
        if (n >= sizeof(m27_aot_hdr) && random() % 2) rehash(f, n);
        if (m27_aot_check(f, n, &x, &v, why, sizeof why) == 0) { accepted++; if (!view_in_bounds(f, n, &v, &x)) bad++; }
        else rejected++;
    }
    CHECK(bad == 0, "fuzz: %d accepted, %d rejected, %d accepted out of bounds", accepted, rejected, bad);
    printf("fuzz: 200000 mutations, %d accepted, %d rejected, %d out of bounds\n", accepted, rejected, bad);
}

/* ------------------------------------------------------ code identity */

/* The identity of an unsigned guest: SHA-256 over the documented stream
 * (m27/aot.h), compared with an independent computation, and different for
 * every change to the header, the layout or one code byte. */
static void test_code_id(void)
{
    uint8_t hdr[48], c0[0x40], c1[0x20], got[M27_AOT_ID_LEN], want[M27_AOT_ID_LEN], other[M27_AOT_ID_LEN];
    for (size_t k = 0; k < sizeof hdr; k++) hdr[k] = (uint8_t)(k * 7 + 1);
    for (size_t k = 0; k < sizeof c0; k++) c0[k] = (uint8_t)(k ^ 0x5a);
    for (size_t k = 0; k < sizeof c1; k++) c1[k] = (uint8_t)(k + 0x80);
    uint64_t lo[2] = {0x4000, 0x9000}, hi[2] = {0x4040, 0x9020};
    const uint8_t *code[2] = {c0, c1};
    m27_aot_code_id(hdr, sizeof hdr, lo, hi, code, 2, got);
    CC_SHA256_CTX c;
    CC_SHA256_Init(&c);
    uint64_t v;
    CC_SHA256_Update(&c, "m27-aot-code-v1", 16);
    v = sizeof hdr; CC_SHA256_Update(&c, &v, 8);
    CC_SHA256_Update(&c, hdr, sizeof hdr);
    v = 2; CC_SHA256_Update(&c, &v, 8);
    CC_SHA256_Update(&c, &lo[0], 8); CC_SHA256_Update(&c, &hi[0], 8); CC_SHA256_Update(&c, c0, sizeof c0);
    CC_SHA256_Update(&c, &lo[1], 8); CC_SHA256_Update(&c, &hi[1], 8); CC_SHA256_Update(&c, c1, sizeof c1);
    CC_SHA256_Final(want, &c);
    CHECK(!memcmp(got, want, sizeof got), "code identity is SHA-256 of tag | len | header | n | (lo | hi | bytes)...");
    m27_aot_code_id(hdr, sizeof hdr, lo, hi, code, 2, other);
    CHECK(!memcmp(got, other, sizeof got), "code identity is deterministic");
#define DIFF(what, change, undo) do { change; m27_aot_code_id(hdr, sizeof hdr, lo, hi, code, 2, other); undo; \
        CHECK(memcmp(got, other, sizeof got) != 0, "code identity changes with %s", what); } while (0)
    DIFF("one code bit (the twin: same header and layout, other code)", c0[0x21] ^= 4, c0[0x21] ^= 4);
    DIFF("the last code byte of the last range", c1[0x1f] ^= 1, c1[0x1f] ^= 1);
    DIFF("one load-command byte", hdr[40] ^= 1, hdr[40] ^= 1);
    DIFF("where a range is mapped", (lo[0] += 0x1000, hi[0] += 0x1000), (lo[0] -= 0x1000, hi[0] -= 0x1000));
    DIFF("a range moved with its end", (lo[1] -= 4, hi[1] -= 4), (lo[1] += 4, hi[1] += 4));
#undef DIFF
    /* the same bytes cut differently between header and code, or between ranges, are another identity */
    uint8_t all[sizeof c0 + sizeof c1];
    memcpy(all, c0, sizeof c0);
    memcpy(all + sizeof c0, c1, sizeof c1);
    uint64_t lo1[1] = {0x4000}, hi1[1] = {0x4000 + sizeof all};
    const uint8_t *code1[1] = {all};
    m27_aot_code_id(hdr, sizeof hdr, lo1, hi1, code1, 1, other);
    CHECK(memcmp(got, other, sizeof got) != 0, "code identity: two ranges are not their concatenation");
    m27_aot_code_id(hdr, sizeof hdr - 8, lo, hi, code, 2, other);
    CHECK(memcmp(got, other, sizeof got) != 0, "code identity: a shorter load-command area is another identity");
    m27_aot_code_id(hdr, sizeof hdr, lo, hi, code, 0, other);
    CHECK(memcmp(got, other, sizeof got) != 0, "code identity: no code range is another identity");
}

/* Which directories hold caches for signed code only. */
static void test_system_dirs(void)
{
    CHECK(m27_aot_dir_is_system(M27_AOT_SYS, "/anything"), "the SYS kind is a system directory");
    CHECK(m27_aot_dir_is_system(M27_AOT_SYS_RO, NULL), "the SYS_RO kind is a system directory");
    CHECK(m27_aot_dir_is_system(M27_AOT_EXPLICIT, M27_AOT_SYS_DIR), "M27_CACHE=/private/var/db/m27 is a system directory");
    CHECK(m27_aot_dir_is_system(M27_AOT_EXPLICIT, M27_AOT_SYS_RO_DIR), "M27_CACHE=/usr/lib/m27/aot is a system directory");
    CHECK(m27_aot_dir_is_system(M27_AOT_EXPLICIT, M27_AOT_SYS_DIR "/sub"), "a directory inside a system directory is one");
    CHECK(!m27_aot_dir_is_system(M27_AOT_EXPLICIT, M27_AOT_SYS_DIR "-other"), "/private/var/db/m27-other is not");
    CHECK(!m27_aot_dir_is_system(M27_AOT_EXPLICIT, "/private/var/db"), "the parent of a system directory is not");
    CHECK(!m27_aot_dir_is_system(M27_AOT_USER, "/Users/alice/Library/Caches/m27"), "a user's directory is not");
    CHECK(!m27_aot_dir_is_system(M27_AOT_EXPLICIT, "/private/tmp/x"), "an explicit scratch directory is not");
    CHECK(!m27_aot_dir_is_system(M27_AOT_EXPLICIT, NULL), "an explicit directory with no path is not");
    /* the directory root's `m27 --aot` writes to is a system one; a user's is not */
    m27_aot_dir d[M27_AOT_MAX_DIRS];
    int w;
    char why[128];
    m27_aot_dirs_for(M27_ENV_OPEN, 0, NULL, NULL, d, &w, why, sizeof why);
    CHECK(w >= 0 && m27_aot_dir_is_system(d[w].kind, d[w].path), "root's write directory is a system directory");
    m27_aot_dirs_for(M27_ENV_OPEN, 501, "/Users/alice", NULL, d, &w, why, sizeof why);
    CHECK(w >= 0 && !m27_aot_dir_is_system(d[w].kind, d[w].path), "a user's write directory is not a system directory");
    m27_aot_dirs_for(M27_ENV_OPEN, 0, NULL, "/var/root/staging", d, &w, why, sizeof why);
    CHECK(w >= 0 && !m27_aot_dir_is_system(d[w].kind, d[w].path), "root's M27_CACHE staging directory is not a system directory");
    m27_aot_dirs_for(M27_ENV_OPEN, 0, NULL, M27_AOT_SYS_DIR, d, &w, why, sizeof why);
    CHECK(w >= 0 && m27_aot_dir_is_system(d[w].kind, d[w].path), "root's M27_CACHE naming a system directory is one");

    /* `m27 --aot --cache DIR`: the command line, so every tier but set-ID; an EXPLICIT directory */
    m27_aot_dir x;
    CHECK(m27_aot_dir_explicit(M27_ENV_OPEN, "/Users/x/c", &x, why, sizeof why) && x.kind == M27_AOT_EXPLICIT && !strcmp(x.path, "/Users/x/c"),
          "--cache in an open process");
    CHECK(m27_aot_dir_explicit(M27_ENV_GUARDED, "/var/root/staging", &x, why, sizeof why) && x.kind == M27_AOT_EXPLICIT &&
          !m27_aot_dir_is_system(x.kind, x.path), "--cache in a guarded process (root): taken, not a system directory");
    CHECK(m27_aot_dir_explicit(M27_ENV_GUARDED, M27_AOT_SYS_RO_DIR, &x, why, sizeof why) && m27_aot_dir_is_system(x.kind, x.path),
          "--cache naming a system directory is one (signed code only)");
    CHECK(!m27_aot_dir_explicit(M27_ENV_SETUGID, "/Users/x/c", &x, why, sizeof why) && strstr(why, "setuid/setgid"), "--cache refused at the set-ID tier (%s)", why);
    CHECK(!m27_aot_dir_explicit(M27_ENV_OPEN, "rel/c", &x, why, sizeof why) && strstr(why, "not an absolute"), "relative --cache refused (%s)", why);
    char big[1100];
    memset(big, 'a', sizeof big - 1);
    big[0] = '/';
    big[sizeof big - 1] = 0;
    CHECK(!m27_aot_dir_explicit(M27_ENV_OPEN, big, &x, why, sizeof why), "over-long --cache refused");
}

/* The feature word from CPUID registers: the Macs in scope, Rosetta, and
 * the operating-system state VEX and EVEX code needs. */
static void test_cpu_decode(void)
{
    char nm[256];
    /* Ivy Bridge (MacBookPro9,2): leaf 1 ECX with PCLMUL, SSSE3, CX16, SSE4.1/4.2, POPCNT, AES, XSAVE, OSXSAVE, AVX, F16C,
     * RDRAND; leaf 7 EBX only FSGSBASE/ERMS-class bits that m27 does not list; XCR0 x87|SSE|AVX */
    m27_cpuid_regs ivb = {.max_leaf = 13, .max_ext = 0x80000008u, .l1_ecx = 0x7fbae3ffu, .l7_ebx = 0x00000281u, .e1_ecx = 1, .xcr0 = 7};
    CHECK(m27_cpu_decode(&ivb) == M27_CPU_BASELINE, "Ivy Bridge: exactly the baseline (%s)", m27_cpu_names(m27_cpu_decode(&ivb), nm, sizeof nm));
    m27_cpuid_regs r = ivb;
    r.l1_ecx &= ~(1u << 27);   /* OSXSAVE clear: AVX state not enabled */
    CHECK(m27_cpu_decode(&r) == (M27_CPU_AES | M27_CPU_PCLMUL | M27_CPU_RDRAND), "AVX without OSXSAVE: no VEX features (%s)",
          m27_cpu_names(m27_cpu_decode(&r), nm, sizeof nm));
    r = ivb; r.xcr0 = 3;       /* the OS saves no YMM state */
    CHECK(!(m27_cpu_decode(&r) & (M27_CPU_AVX | M27_CPU_F16C)), "AVX with XCR0 lacking YMM state: no VEX features");
    /* Rosetta as measured: leaf 1 ECX 0298220f, leaf 7 zero */
    m27_cpuid_regs ro = {.max_leaf = 13, .max_ext = 0x80000008u, .l1_ecx = 0x0298220fu};
    CHECK(m27_cpu_decode(&ro) == (M27_CPU_AES | M27_CPU_PCLMUL), "Rosetta: aes,pclmul (%s)", m27_cpu_names(m27_cpu_decode(&ro), nm, sizeof nm));
    /* Rosetta with ROSETTA_ADVERTISE_AVX=1: leaf 1 ECX 7ed8320f, leaf 7 EBX 128, LZCNT */
    m27_cpuid_regs rh = {.max_leaf = 13, .max_ext = 0x80000008u, .l1_ecx = 0x7ed8320fu, .l7_ebx = 0x128, .e1_ecx = 0x21, .xcr0 = 7};
    uint64_t want = M27_CPU_BASELINE | M27_CPU_FMA | M27_CPU_MOVBE | M27_CPU_AVX2 | M27_CPU_BMI1 | M27_CPU_BMI2 | M27_CPU_LZCNT;
    CHECK(m27_cpu_decode(&rh) == want, "Rosetta advertising AVX: a Haswell-class word (%s)", m27_cpu_names(m27_cpu_decode(&rh), nm, sizeof nm));
    r = rh; r.max_leaf = 6;    /* leaf 7 not enumerated: its registers are not read */
    CHECK(!(m27_cpu_decode(&r) & (M27_CPU_AVX2 | M27_CPU_BMI1 | M27_CPU_BMI2)), "leaf 7 ignored when the maximum leaf is below 7");
    /* Skylake-SP class: AVX-512 F/DQ/BW/VL, only with opmask and ZMM state in XCR0 */
    m27_cpuid_regs sx = rh;
    sx.l7_ebx |= 1u << 16 | 1u << 17 | 1u << 30 | 1u << 31;
    sx.xcr0 = 0xE7;
    uint64_t f512 = M27_CPU_AVX512F | M27_CPU_AVX512DQ | M27_CPU_AVX512BW | M27_CPU_AVX512VL;
    CHECK((m27_cpu_decode(&sx) & f512) == f512, "AVX-512 with XCR0 E7: avx512f,dq,bw,vl");
    sx.xcr0 = 7;
    CHECK(!(m27_cpu_decode(&sx) & f512) && (m27_cpu_decode(&sx) & M27_CPU_AVX2), "AVX-512 without ZMM state in XCR0: none of it, AVX2 kept");
    CHECK((M27_CPU_JIT & ~M27_CPU_BASELINE) == 0, "every feature the translator consults is in the baseline");
    /* --cpu: clear-only against the host */
    uint64_t p = 0;
    char why[160];
    CHECK(m27_cpu_profile_parse("baseline", want, &p, why, sizeof why) && p == M27_CPU_BASELINE, "--cpu=baseline on a Haswell-class host");
    CHECK(m27_cpu_profile_parse("baseline", M27_CPU_AES | M27_CPU_PCLMUL, &p, why, sizeof why) && p == (M27_CPU_AES | M27_CPU_PCLMUL),
          "--cpu=baseline on Rosetta: never a feature the host lacks");
    CHECK(m27_cpu_profile_parse("host", want, &p, why, sizeof why) && p == want, "--cpu=host");
    CHECK(m27_cpu_profile_parse("0x1", want, &p, why, sizeof why) && p == M27_CPU_AES, "--cpu=0x1");
    CHECK(m27_cpu_profile_parse("ffffffff", M27_CPU_AES, &p, why, sizeof why) && p == M27_CPU_AES, "--cpu=ffffffff only clears");
    CHECK(m27_cpu_profile_parse("0", want, &p, why, sizeof why) && p == 0, "--cpu=0");
    CHECK(!m27_cpu_profile_parse("avx2", want, &p, why, sizeof why) && strstr(why, "expected"), "--cpu=avx2 refused (%s)", why);
    CHECK(!m27_cpu_profile_parse("0x", want, &p, why, sizeof why), "--cpu=0x refused");
    CHECK(!m27_cpu_profile_parse("-1", want, &p, why, sizeof why), "--cpu=-1 refused");
}

int main(int argc, char **argv)
{
    if (argc != 2) { fprintf(stderr, "usage: policy TMPDIR\n"); return 2; }
    CHECK(sizeof(m27_aot_hdr) == 144 && __builtin_offsetof(m27_aot_hdr, id_kind) == 96 && __builtin_offsetof(m27_aot_hdr, code_id) == 104 &&
          __builtin_offsetof(m27_aot_hdr, nblocks) == 64 && __builtin_offsetof(m27_aot_hdr, cpu_profile) == 136 && M27_AOT_VERSION == 8,
          "format v8 header: 144 bytes, id_kind at 96, code_id at 104, cpu_profile at 136 (the tools read these offsets)");
    test_cpu_decode();
    test_code_id();
    test_system_dirs();
    test_tiers();
    test_dirs();
    test_system_only();
    test_stat_rules(argv[1]);
    test_acl(argv[1]);
    test_format();
    printf("%s policy: %d checks passed, %d failed\n", fails ? "FAIL" : "PASS", passes, fails);
    return fails ? 1 : 0;
}
