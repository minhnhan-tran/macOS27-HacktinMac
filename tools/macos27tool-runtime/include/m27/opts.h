/*
 * m27 — the switch snapshot. Every M27_* switch and m27_* boot argument m27
 * honours is read once, at startup, before any guest code or second thread runs, into
 * m27_opt; readers load its fields. A guest's own setenv cannot change m27's
 * behaviour, and no hot path calls getenv.
 */
#ifndef M27_OPTS_H
#define M27_OPTS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    int verbose;                /* -v/-vv, M27_VERBOSE=N, PID 1: m27=v|V, m27_verbose=N */
    bool interp, stats;         /* --interp/M27_INTERP, --stats (PID 1: m27=i|s) */
    bool allow_unsigned;        /* --allow-unsigned (M27_FLAGS u, open tier): run an unsigned CLI guest */
    unsigned watchdog;          /* M27_WATCHDOG=N seconds (open tier: it takes SIGALRM and exits 124) */
    unsigned jit_cache_mb;      /* M27_JIT_CACHE_MB=N: translated-code space (0 = default) */
    unsigned aot_limit_kb;      /* M27_AOT_LIMIT_KB=N: AOT loading's memory limit instead of the kernel's (tests; jit.c aot_fits) */
    uint64_t cpu_clear;         /* M27_CPU_FEATURES=HEX (tests): the optional CPU features (m27/cpufeat.h) the mask
                                   leaves out, which m27 then never uses; it can only clear bits (a value that is not
                                   hexadecimal clears all of them) */
    /* boolean knobs: environment M27_<NAME> (empty or 0 = off); boot argument m27_<name>=1 for those listed below */
    bool nx_stats, crash_trace, jit_helpers_only, no_aot, no_chain, no_msgsend, no_closures, no_swiftreg,
         no_block_thunks, no_fixup_thunks, no_struct_thunks, aot_learn, no_jit_memory, bridge_check, bridge_abort,
         no_varsniff, no_libval, bridge_stats, no_ifunits, no_swift_thunks, no_thunks, no_swift_values,
         diag_time,              /* M27_DIAG_TIME: diagnostics lines stamped with the mach clock, and startup phase
                                    lines (runtime.c m27_phase) */
         native_app_libs,        /* M27_NATIVE_APP_LIBS: try app libraries natively first (open tier only) */
         native_libcxx,          /* M27_NATIVE_LIBCXX: libc++ stays native (no guest libc++ from the arm64e cache) */
         sigreturn_kernel,       /* M27_SIGRETURN_KERNEL: m27's signal handlers return through __sigreturn (signals.c) */
         sigreturn_user,         /* M27_SIGRETURN_USER: resume in user space under Rosetta too (tests; signals.c) */
         no_probe,               /* M27_NO_PROBE: no fault-tolerant reads; blocks.c reads candidates through Mach (signals.c) */
         code_alias,             /* M27_CODE_ALIAS: m27 reads guest code from an alias, the guest's text pages poisoned (code.c) */
         libval_user,            /* M27_LIBVAL_USER: library validation's signer checks in user space, as on an x86_64 kernel (tests; libval.c) */
         swift_value_passthrough, /* M27_SWIFT_VALUE_PASSTHROUGH (open tier, no boot argument): experimental, for
                                    triage only, accepting memory corruption: a Swift value m27 cannot certify goes on
                                    to native code instead of being refused (swiftvalue.c decline) */
         swift_value_strict,     /* M27_SWIFT_VALUE_STRICT (tests; a M27_TEST_SWITCHES build only): refuse a Swift
                                    value no row or rule certifies even where its types are listed for report-only
                                    (swiftvalue.c uncertified, report_listed) */
         swift_value_report,     /* boot argument m27_swift_value_report=1 (every process, every build) or
                                    M27_SWIFT_VALUE_REPORT (open tier): a Swift value no row or rule certifies is
                                    reported and passed as found whatever its types, as in v0.0.1, accepting the
                                    corruption a wrong guess causes; for a boot that lists them
                                    (tools/m27_sv_reportlist.py) or one an unlisted type stops */
         swift_value_audit,      /* boot argument m27_swift_value_audit=1 (every process, every tier) or
                                    M27_SWIFT_VALUE_AUDIT (open tier): a survey image's setting, accepting memory
                                    corruption as the pass-through does: a value m27 cannot certify goes on, and one
                                    machine-readable "m27-swift-audit" record per (type, reason) names it
                                    (swiftvalue.c audit_record); only in a survey build (make SURVEY=1) or a
                                    test build */
         aot_use,                /* M27_AOT_USE (guarded too) / boot argument m27_aot_use=1: AOT use records to the
                                    unified log (aot_use.c, tools/m27-aot-use) */
         swift_keys,             /* M27_SWIFT_KEYS, boot argument m27_swift_keys=1 (every process): one
                                    machine-readable "m27-swift-key" record per Swift type the value walk looks up
                                    in its facts table (swiftvalue/Facts.c key_met); prints only */
         crash;                  /* boot argument m27_crash=1 (every process; M27_CRASH in tests): diagnostics also to
                                    /dev/console and /private/var/log/m27 (runtime.c m27_diag_init) */
    bool swift_value_audit_ignored;  /* the audit was asked for and this build ignores it (said once, swiftvalue.c) */
    char cache[1024];           /* M27_CACHE ("" = unset) */
    char guest_root[1024];      /* M27_GUEST_ROOT ("" = unset) */
    char aot_list_dir[1024];    /* M27_AOT_LIST_DIR: where m27 --aot with no FILE reads its lists (tests; "" = /usr/lib/m27) */
    char aot_free_mb[128];      /* M27_AOT_FREE_MB=N[,N...]: free megabytes m27 --aot --auto measures, one value per
                                   measurement, the last repeated (tests; "" = statfs; main.c aot_space) */
    char if_dir[1024];          /* M27_IF_DIR: interface units (m27/bridge.h) instead of /usr/lib/m27/if ("" = unset) */
    char async_contract_library[1024]; /* explicitly selected own-code async provider; open tier only */
    char trust_list[1024];      /* M27_TRUST_LIST: extra cdhash list for CLI guests (guest_sig.c; "" = unset) */
    char diag_dir[1024];        /* M27_DIAG_DIR: diagnostics log directory (runtime.c; "" = unset: m27_crash=1's) */
    char swift_walk_facts[1024]; /* M27_SWIFT_WALK_FACTS: a fixture of the Swift walk's facts table (tests;
                                    swiftvalue/Facts.c; "" = unset) */
    char swift_value_list[1024]; /* M27_SWIFT_VALUE_LIST: more report-only type keys, one per line (tests;
                                    swiftvalue.c report_listed) */
    char swift_guest_facts[1024]; /* M27_SWIFT_GUEST_FACTS: a fixture of the guest-type rows (tests; Facts.c) */
    char swift_pop[16];         /* M27_SWIFT_POP: "signature" or "frame" ("" = both; runtime.c callee_pop) */
    char ignored[512];          /* comma-separated switches the tier ignored */
    bool ready;
} m27_opts_t;

extern m27_opts_t m27_opt;

/* Pure (src/opts.c, in libm27core.a): fill o from an environment block, the
 * M27_FLAGS letters (v, V, i, s, u; NULL = none), the kernel boot arguments
 * (NULL = none) and the environment tier (m27/aot.h). Precedence, lowest to
 * highest: defaults, boot arguments, flags, environment.
 *
 * Boot arguments are set by root and apply in every tier, so only these are
 * read: in every process m27_crash=1, m27_nx_stats=1, m27_crash_trace=1,
 * m27_bridge_stats=1, m27_diag_time=1, m27_swift_keys=1, m27_aot_use=1, m27_no_aot=1, m27_no_chain=1, m27_jit_helpers_only=1,
 * m27_swift_value_audit=1 (a survey build only, make SURVEY=1: swiftvalue.c audit_record);
 * in PID 1 also m27=FLAGS (v, V, i, s) and m27_verbose=N. No boot argument
 * turns a check off (there is no m27_no_libval and no m27=u). A build with
 * M27_TEST_SWITCHES defined also reads the bring-up knobs that change
 * bridging or signal return (src/opts.c: BOOT_DEV).
 *
 * flags and the environment are filtered by m27_env_switch_allowed(). The
 * test switches (M27_LIBVAL_USER, M27_SIGRETURN_USER, M27_AOT_LIMIT_KB,
 * M27_BRIDGE_ABORT, M27_NO_JIT_MEMORY, M27_CODE_ALIAS, M27_CPU_FEATURES,
 * M27_AOT_LIST_DIR, M27_AOT_FREE_MB, M27_CRASH, M27_SWIFT_VALUE_STRICT) are read only by a
 * M27_TEST_SWITCHES build, from an open-tier environment. */
void m27_opts_parse(m27_opts_t *o, char **envp, const char *flags, const char *bootargs, bool pid1, int tier);
/* Runtime (src/trust.c): once per process, from environ and kern.bootargs. */
void m27_opts_init(const char *flags);

#endif
