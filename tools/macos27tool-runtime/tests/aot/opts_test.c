/*
 * Unit test of m27_opts_parse (src/opts.c, linked from libm27core.a): the
 * switch snapshot's precedence, boot-argument matching and environment tiers.
 */
#include "m27/opts.h"
#include "m27/cpufeat.h"
#include "m27/aot.h"
#include <stdio.h>
#include <string.h>

static int fails, n;
#define CHECK(c, ...) do { n++; if (!(c)) { fails++; printf("FAIL "); printf(__VA_ARGS__); printf("\n"); } } while (0)

int main(void)
{
    m27_opts_t o;
    char *none[] = {NULL};
    const char *ba = "m27=vi m27_verbose=2 m27_no_chain=1 m27_crash_trace=1";
    m27_opts_parse(&o, none, NULL, ba, true, M27_ENV_GUARDED);
    CHECK(o.verbose == 2 && o.interp && o.no_chain && o.crash_trace, "PID 1 boot args: verbose %d interp %d", o.verbose, o.interp);
    m27_opts_parse(&o, none, NULL, ba, false, M27_ENV_OPEN);
    CHECK(o.verbose == 0 && !o.interp && o.no_chain && o.crash_trace, "m27=/m27_verbose= are PID 1 only; knobs everywhere");
    char *e3[] = {"M27_NO_CLOSURES=1", "M27_VERBOSE=1", "M27_CACHE=/x", "M27_GUEST_ROOT=/g", NULL};
    m27_opts_parse(&o, e3, NULL, NULL, false, M27_ENV_OPEN);
    CHECK(o.no_closures && o.verbose == 1 && !strcmp(o.cache, "/x") && !strcmp(o.guest_root, "/g"), "environment, open");
    m27_opts_parse(&o, none, NULL, "m27_no_chainx=1 xm27_no_aot=1 m27_no_aot=10 m27_no_swiftreg m27_no_msgsend=0", false, M27_ENV_OPEN);
    CHECK(!o.no_chain && !o.no_aot && !o.no_swiftreg && !o.no_msgsend, "boot arguments match exactly (key and =1)");
    m27_opts_parse(&o, none, NULL, "m27_no_chain=1 m27_no_aot=1 m27_nx_stats=1", false, M27_ENV_SETUGID);
    CHECK(o.no_chain && o.no_aot && o.nx_stats, "listed boot arguments honoured in every tier");
    char *e6[] = {"M27_NO_CHAIN=", "M27_NO_AOT=0", "M27_INTERP=0", "M27_BRIDGE_STATS=yes", NULL};
    m27_opts_parse(&o, e6, NULL, NULL, false, M27_ENV_OPEN);
    CHECK(!o.no_chain && !o.no_aot && !o.interp && o.bridge_stats, "an empty value or 0 turns a switch off, any other on");
    m27_opts_parse(&o, e6, "i", NULL, false, M27_ENV_OPEN);
    CHECK(o.interp, "M27_INTERP=0 counts as absent: the i flag stands");
    char *e7[] = {"M27_VERBOSE=3", NULL};
    m27_opts_parse(&o, e7, "vs", NULL, false, M27_ENV_OPEN);
    CHECK(o.verbose == 3 && o.stats, "environment beats M27_FLAGS");
    char *e8[] = {"M27_CACHE=/evil", "M27_GUEST_ROOT=/evil", "M27_AOT_LEARN=1", "M27_NO_CLOSURES=1", "M27_VERBOSE=2",
                  "M27_NO_AOT=1", "M27_INTERP=1", NULL};
    m27_opts_parse(&o, e8, "s", NULL, false, M27_ENV_GUARDED);
    CHECK(!o.cache[0] && !o.guest_root[0] && !o.aot_learn && !o.no_closures && !o.stats && o.verbose == 2 && o.no_aot && o.interp,
          "guarded: code/path/bridging/flags ignored, diagnostics and off switches kept");
    CHECK(strstr(o.ignored, "M27_CACHE") && strstr(o.ignored, "M27_FLAGS") && strstr(o.ignored, "M27_AOT_LEARN"), "ignored list: %s", o.ignored);
    m27_opts_parse(&o, e8, "v", NULL, false, M27_ENV_SETUGID);
    CHECK(!o.cache[0] && !o.guest_root[0] && o.verbose == 0 && !o.no_aot && !o.interp && !o.stats, "setugid: no switch at all");
    CHECK(strstr(o.ignored, "M27_NO_AOT") && strstr(o.ignored, "M27_INTERP") && strstr(o.ignored, "M27_VERBOSE"),
          "setugid ignored list: %s", o.ignored);
    char *e10[] = {"M27_NO_LIBVAL=1", NULL};
    m27_opts_parse(&o, e10, NULL, NULL, false, M27_ENV_GUARDED);
    CHECK(!o.no_libval && strstr(o.ignored, "M27_NO_LIBVAL"), "M27_NO_LIBVAL ignored in a guarded process");
    m27_opts_parse(&o, e10, NULL, NULL, false, M27_ENV_OPEN);
    CHECK(o.no_libval, "M27_NO_LIBVAL in an ordinary process");
    m27_opts_parse(&o, none, NULL, "m27_no_libval=1", true, M27_ENV_GUARDED);
    CHECK(!o.no_libval, "no boot argument turns library validation off (PID 1)");
    m27_opts_parse(&o, none, NULL, "m27_no_libval=1", false, M27_ENV_OPEN);
    CHECK(!o.no_libval, "no boot argument turns library validation off (ordinary process)");
    m27_opts_parse(&o, none, NULL, "m27=u m27=vu", true, M27_ENV_GUARDED);
    CHECK(!o.allow_unsigned && o.verbose == 1, "boot argument m27=u does not allow unsigned guests");
    /* the boot-argument list (m27/opts.h): every knob by name, in PID 1, a daemon and a set-ID process */
    {
        static const char *yes[] = {"nx_stats", "crash_trace", "bridge_stats", "no_aot", "no_chain", "jit_helpers_only", "aot_use", NULL};
        static const char *dev[] = {"no_msgsend", "no_closures", "no_swiftreg", "no_block_thunks", "no_fixup_thunks",
                                    "no_struct_thunks", "bridge_check", "no_varsniff", "no_ifunits", "no_swift_thunks",
                                    "no_swift_values", "native_libcxx", "no_thunks", "sigreturn_kernel", "no_probe", NULL};
        static const char *never[] = {"no_libval", "aot_learn", "no_jit_memory", "bridge_abort", "sigreturn_user", "code_alias",
                                      "libval_user", "swift_value_passthrough", "swift_value_strict", "interp", "stats", "allow_unsigned", "watchdog", "cache", "guest_root",
                                      "if_dir", "trust_list", "diag_dir", "async_contract_library", "aot_limit_kb",
                                      "jit_cache_mb", "swift_pop", "cpu_features", "aot_free_mb", "swift_walk_facts", "swift_value_list", NULL};
        /* boot argument in every tier, environment open only (opts.c BOOT_SURVEY) */
        static const char *survey[] = {"swift_value_audit", NULL};
        /* boot argument in every tier and build, environment open only (opts.c BOOT_ANY) */
        static const char *any[] = {"swift_value_report", NULL};
        static const int tiers[] = {M27_ENV_OPEN, M27_ENV_GUARDED, M27_ENV_SETUGID};
        m27_opts_t zero;
        m27_opts_parse(&zero, none, NULL, NULL, false, M27_ENV_OPEN);
        int nknobs = 0;
        for (int pass = 0; pass < 5; pass++) {
            const char **l = pass == 0 ? yes : pass == 1 ? dev : pass == 2 ? survey : pass == 3 ? never : any;
            bool want = pass == 0 || pass == 4 || (pass == 1 && m27_test_switches) ||
                        (pass == 2 && (m27_test_switches || m27_survey_build));
            for (; *l; l++, nknobs += pass != 3) {
                char arg[96], up[64] = "M27_";
                snprintf(arg, sizeof arg, "m27_%s=1 m27_%s=/x", *l, *l);
                for (int t = 0; t < 3; t++) {
                    m27_opts_parse(&o, none, NULL, arg, t == 1, tiers[t]);
                    o.ready = zero.ready;
                    /* a survey knob a release build ignores is said so once: the only trace it leaves */
                    if (pass == 2) CHECK(o.swift_value_audit_ignored == !want, "m27_%s=1: ignored flag at tier %d", *l, tiers[t]);
                    o.swift_value_audit_ignored = false;
                    bool set = memcmp(&o, &zero, sizeof o) != 0;
                    CHECK(set == want, "boot argument m27_%s=1 %s at tier %d", *l, want ? "read" : "not read", tiers[t]);
                }
                /* what a release build reads from the boot arguments, a guarded process may read from its environment */
                for (size_t i = 0; (*l)[i]; i++) up[4 + i] = (char)((*l)[i] >= 'a' && (*l)[i] <= 'z' ? (*l)[i] - 32 : (*l)[i]);
                if (pass == 0) CHECK(m27_env_switch_allowed(up, M27_ENV_GUARDED), "%s: boot-listed, so guarded-allowed", up);
                if (pass == 1) CHECK(!m27_env_switch_allowed(up, M27_ENV_GUARDED) && !m27_env_test_switch(up), "%s: bring-up knob", up);
                if (pass == 2) CHECK(!m27_env_switch_allowed(up, M27_ENV_GUARDED) &&
                                     m27_env_switch_allowed(up, M27_ENV_OPEN) == (m27_test_switches || m27_survey_build),
                                     "%s: survey knob, environment open only, survey or test builds only", up);
                if (pass == 4) CHECK(!m27_env_switch_allowed(up, M27_ENV_GUARDED) && m27_env_switch_allowed(up, M27_ENV_OPEN) &&
                                     !m27_env_test_switch(up), "%s: boot argument everywhere, environment open only", up);
            }
        }
        CHECK(nknobs == 24, "boot-argument lists name 24 knobs (%d)", nknobs);
    }
    /* the test switches: read from an ordinary environment by a M27_TEST_SWITCHES build only */
    {
        char *et[] = {"M27_LIBVAL_USER=1", "M27_SIGRETURN_USER=1", "M27_AOT_LIMIT_KB=64", "M27_BRIDGE_ABORT=1",
                      "M27_NO_JIT_MEMORY=1", "M27_CODE_ALIAS=1", "M27_CPU_FEATURES=1", "M27_AOT_FREE_MB=5,6", "M27_CRASH=1",
                      "M27_SWIFT_VALUE_STRICT=1", "M27_SWIFT_VALUE_LIST=/l", NULL};
        m27_opts_parse(&o, et, NULL, NULL, false, M27_ENV_OPEN);
        bool all = o.libval_user && o.sigreturn_user && o.aot_limit_kb == 64 && o.bridge_abort && o.no_jit_memory && o.code_alias &&
                   o.cpu_clear == (M27_CPU_ALL & ~M27_CPU_AES) && !strcmp(o.aot_free_mb, "5,6") && o.crash &&
                   o.swift_value_strict && !strcmp(o.swift_value_list, "/l");
        bool any = o.libval_user || o.sigreturn_user || o.aot_limit_kb || o.bridge_abort || o.no_jit_memory || o.code_alias || o.cpu_clear ||
                   o.aot_free_mb[0] || o.crash || o.swift_value_strict || o.swift_value_list[0];
        if (m27_test_switches) CHECK(all, "test switches read in a test build (open)");
        else CHECK(!any && strstr(o.ignored, "M27_CODE_ALIAS") && strstr(o.ignored, "M27_LIBVAL_USER"),
                   "test switches not read by a build without M27_TEST_SWITCHES (ignored: %s)", o.ignored);
        m27_opts_parse(&o, et, NULL, NULL, false, M27_ENV_GUARDED);
        any = o.libval_user || o.sigreturn_user || o.aot_limit_kb || o.bridge_abort || o.no_jit_memory || o.code_alias || o.cpu_clear ||
              o.aot_free_mb[0] || o.crash || o.swift_value_strict || o.swift_value_list[0];
        CHECK(!any, "test switches never read in a guarded process");
        /* M27_CPU_FEATURES only clears: a mask, baseline, host; anything else clears every optional feature */
        static const struct { const char *v; uint64_t clear; } cf[] = {
            {"M27_CPU_FEATURES=0", M27_CPU_ALL}, {"M27_CPU_FEATURES=0x3", M27_CPU_ALL & ~3ull},
            {"M27_CPU_FEATURES=baseline", M27_CPU_ALL & ~M27_CPU_BASELINE}, {"M27_CPU_FEATURES=host", 0},
            {"M27_CPU_FEATURES=ffffffffffffffff", 0}, {"M27_CPU_FEATURES=avx2", M27_CPU_ALL}, {"M27_CPU_FEATURES=", M27_CPU_ALL},
        };
        for (size_t k = 0; k < sizeof cf / sizeof *cf; k++) {
            char *ec[] = {(char *)cf[k].v, NULL};
            m27_opts_parse(&o, ec, NULL, NULL, false, M27_ENV_OPEN);
            CHECK(o.cpu_clear == (m27_test_switches ? cf[k].clear : 0), "%s clears %#llx", cf[k].v, (unsigned long long)o.cpu_clear);
        }
    }
    /* switches that make a correct program incorrect or take a signal are not "off switches": open tier only */
    {
        char *eb[] = {"M27_NO_VARSNIFF=1", "M27_NO_THUNKS=1", "M27_NATIVE_LIBCXX=1", "M27_NATIVE_APP_LIBS=1", "M27_SIGRETURN_KERNEL=1", "M27_NO_PROBE=1",
                      "M27_WATCHDOG=5", "M27_NO_IFUNITS=1", "M27_BRIDGE_CHECK=1", NULL};
        m27_opts_parse(&o, eb, NULL, NULL, false, M27_ENV_OPEN);
        CHECK(o.no_varsniff && o.no_thunks && o.native_libcxx && o.native_app_libs && o.sigreturn_kernel && o.no_probe && o.watchdog == 5 &&
              o.no_ifunits && o.bridge_check, "bridging, signal and watchdog switches, open");
        for (int t = M27_ENV_GUARDED; t <= M27_ENV_SETUGID; t++) {
            m27_opts_parse(&o, eb, NULL, NULL, false, t);
            CHECK(!o.no_varsniff && !o.no_thunks && !o.native_libcxx && !o.native_app_libs && !o.sigreturn_kernel && !o.no_probe && !o.watchdog &&
                  !o.no_ifunits && !o.bridge_check && strstr(o.ignored, "M27_WATCHDOG") && strstr(o.ignored, "M27_NO_VARSNIFF"),
                  "bridging, signal and watchdog switches ignored at tier %d (%s)", t, o.ignored);
        }
    }
    m27_opts_parse(&o, none, NULL, "m27_native_app_libs=1", true, M27_ENV_OPEN);
    CHECK(!o.native_app_libs, "native app libraries cannot be selected by a boot argument");
    /* the typed boundary (m27/bridge.h): stats prints; check binds more, no_ifunits changes how calls are made and
     * M27_IF_DIR chooses code (an interface unit decides how native calls are made) */
    char *e11[] = {"M27_BRIDGE_STATS=1", "M27_BRIDGE_CHECK=1", "M27_NO_IFUNITS=1", "M27_IF_DIR=/u", NULL};
    m27_opts_parse(&o, e11, NULL, NULL, false, M27_ENV_OPEN);
    CHECK(o.bridge_stats && o.bridge_check && o.no_ifunits && !strcmp(o.if_dir, "/u"), "bridge switches, open");
    m27_opts_parse(&o, e11, NULL, NULL, false, M27_ENV_GUARDED);
    CHECK(o.bridge_stats && !o.bridge_check && !o.no_ifunits && !o.if_dir[0] && strstr(o.ignored, "M27_IF_DIR"),
          "bridge switches, guarded: only M27_BRIDGE_STATS");
    m27_opts_parse(&o, e11, NULL, NULL, false, M27_ENV_SETUGID);
    CHECK(!o.bridge_stats && !o.bridge_check && !o.no_ifunits && !o.if_dir[0], "bridge switches, setugid: none");
    /* M27_DIAG_DIR names a directory m27 creates a file in: ordinary processes only */
    char *e12[] = {"M27_DIAG_DIR=/d", NULL};
    m27_opts_parse(&o, e12, NULL, NULL, false, M27_ENV_OPEN);
    CHECK(!strcmp(o.diag_dir, "/d"), "M27_DIAG_DIR, open");
    m27_opts_parse(&o, e12, NULL, NULL, false, M27_ENV_GUARDED);
    CHECK(!o.diag_dir[0] && strstr(o.ignored, "M27_DIAG_DIR"), "M27_DIAG_DIR ignored in a guarded process");
    m27_opts_parse(&o, none, NULL, "m27_bridge_stats=1 m27_jit_helpers_only=1", false, M27_ENV_SETUGID);
    CHECK(o.bridge_stats && o.jit_helpers_only, "boot arguments m27_bridge_stats=1 m27_jit_helpers_only=1 in every tier");
    m27_opts_parse(&o, none, NULL, "m27_diag_time=1", true, M27_ENV_GUARDED);
    CHECK(o.diag_time, "boot argument m27_diag_time=1 in PID 1");
    /* m27_crash=1: the diagnostics channel of every process of the boot (runtime.c m27_diag_init) */
    for (int t = M27_ENV_OPEN; t <= M27_ENV_SETUGID; t++) {
        m27_opts_parse(&o, none, NULL, "m27_crash=1", t == M27_ENV_GUARDED, t);
        CHECK(o.crash, "boot argument m27_crash=1 at tier %d", t);
    }
    m27_opts_parse(&o, none, NULL, "m27_crash=0 xm27_crash=1 m27_crashx=1 m27_crash=11", false, M27_ENV_OPEN);
    CHECK(!o.crash, "m27_crash matches exactly (key and =1)");
    char *e9[] = {"M27_NO_CHAINX=1", "XM27_NO_AOT=1", "M27_NO_AO=1", NULL};
    m27_opts_parse(&o, e9, NULL, NULL, false, M27_ENV_OPEN);
    CHECK(!o.no_chain && !o.no_aot, "environment names match exactly");
    m27_opts_parse(&o, none, "V", NULL, false, M27_ENV_OPEN);
    CHECK(o.verbose == 2, "M27_FLAGS V alone is -vv (verbose %d)", o.verbose);
    m27_opts_parse(&o, none, NULL, "m27=V", true, M27_ENV_GUARDED);
    CHECK(o.verbose == 2, "boot argument m27=V alone is -vv (verbose %d)", o.verbose);
    m27_opts_parse(&o, none, "u", NULL, false, M27_ENV_OPEN);
    CHECK(o.allow_unsigned, "M27_FLAGS u allows an unsigned guest (open)");
    m27_opts_parse(&o, none, "u", NULL, false, M27_ENV_GUARDED);
    CHECK(!o.allow_unsigned, "M27_FLAGS u ignored in a guarded process");
    char provider[]="M27_ASYNC_CONTRACT_LIBRARY=/contract.dylib";
    char *ep[]={provider,NULL};
    m27_opts_parse(&o,ep,NULL,NULL,false,M27_ENV_OPEN);
    CHECK(!strcmp(o.async_contract_library,"/contract.dylib"),"async provider, open");
    provider[sizeof provider-2]='x';
    CHECK(!strcmp(o.async_contract_library,"/contract.dylib"),"async provider is a startup snapshot");
    m27_opts_parse(&o,ep,NULL,NULL,false,M27_ENV_GUARDED);
    CHECK(!o.async_contract_library[0] && strstr(o.ignored,"M27_ASYNC_CONTRACT_LIBRARY"),"async provider ignored, guarded");
    m27_opts_parse(&o,ep,NULL,NULL,false,M27_ENV_SETUGID);
    CHECK(!o.async_contract_library[0],"async provider ignored, setugid");
    printf("%s opts: %d/%d ok\n", fails ? "FAIL" : "PASS", n - fails, n);
    return fails != 0;
}
