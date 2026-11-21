/* m27 — hybrid runtime: guest threads, dispatch, native bridge, traps. */
#ifndef M27_RUNTIME_H
#define M27_RUNTIME_H

#include "m27/cpu.h"
#include "m27/macho.h"
#include "m27/code.h"
#include "m27/gmap.h"
#include <setjmp.h>

/* Guest return sentinel: a guest `ret` to this address ends m27_run(). It is
 * a canonical 47-bit address, so a guest that strips its own return address
 * (XPACLRI, XPACI: a64_xpac) keeps it, as it keeps a real one natively; no
 * process can map it (above MACH_VM_MAX_ADDRESS for x86_64 and arm64).
 * bridge_x86_64.S has a copy. */
#define M27_RETURN_MAGIC 0x00007ffffffff000ull

#include <signal.h>

struct m27_thread;
struct m27_swift_value_scope;

/* One guest segment: a m27_run() invocation, i.e. guest frames entered from
 * native code (m27_call_guest, m27_callback, a signature callback) and run
 * until the guest returns to M27_RETURN_MAGIC. Segments nest through native
 * frames; the record lives in m27_run's frame (see unwind.c). */
typedef struct m27_seg {
    struct m27_seg *prev;      /* enclosing segment, or NULL */
    struct m27_thread *t;
    const m27_cpu *outer;      /* enclosing segment's guest state, saved by whoever entered this one */
    int depth;                 /* t->depth while this segment runs */
    int fault_at_entry;        /* t->in_fault_handler when the segment was entered */
    int nest_at_entry;         /* t->sig_nest when the segment was entered */
    struct m27_swift_value_scope *swift_values_at_entry;
    unsigned cpu_saves_at_entry;   /* t->cpu_saves_n while this segment runs guest code */
    jmp_buf jb;                /* re-entry point: a guest longjmp from an inner segment
                                * (m27_longjmp_segment), a signal handler that moved pc (signals.c) */
    /* run_loop -> native_step, for the native call about to run (runtime.c, native-target cache):
     * nc_hit: the cache gave the plan (nc_plan) for native target nc_target; else nc_key, when
     * nonzero, is the address the guest branched to (before shared-cache redirects, which set x1 to
     * nc_x1 when nonzero) and nc_gen the cache generation its classification started in */
    const struct m27_plan *nc_plan;
    uint64_t nc_target, nc_key, nc_x1, nc_gen;
    bool nc_hit;
} m27_seg;

/* An exception whose guest handler the search phase found (unwind.c). */
typedef struct m27_eh_rec {
    void *ue;                  /* struct _Unwind_Exception * */
    m27_seg *seg;
    int depth;
    uint64_t cfa, lp, adj;
    int64_t sel;
    const uint8_t *lsda, *action;
} m27_eh_rec;

#define M27_EH_RECS 8

#define M27_JIT_JC_SLOTS 2048   /* a power of two */
#define M27_NCACHE_SLOTS 256    /* a power of two */
#define M27_STEP_RING 16        /* a power of two */
#define M27_SWIFT_MADE_RING 64  /* a power of two */
typedef struct m27_thread {
    m27_cpu cpu;               /* must be first (16-byte aligned) */
    uint8_t *stack;
    size_t stack_size;
    int depth;                 /* nested m27_run() levels on this thread */
    uint64_t native_calls, callbacks, traps, steps, blocks;
    unsigned swift_objects_building;
    unsigned swift_results_resolving;
    bool async_contract_resolving;
    struct m27_swift_value_scope *swift_values;
    /* Nils a guest call of an Optional witness made and m27 wrote in the
     * guest's encoding (swiftvalue.c witness_to_guest), a ring: a callback
     * returning such a value to native code as its indirect result gets the
     * native encoding back (m27_swift_witness_result). Nested callbacks
     * share it; each entry keeps its index, so one a later note overwrote
     * is seen as lost (counted), not taken for another. */
    struct { uint64_t value, at, native, arm; unsigned idx; } swift_made[M27_SWIFT_MADE_RING];
    unsigned swift_made_n;
    /* The guest state a native-to-guest call saves and restores (1 KB),
     * from the fifth nesting level on kept here instead of on the native
     * stack: a LIFO in a mapping made at first use (runtime.c
     * cpu_save_push), reset by each segment to its entry count. */
    m27_cpu *cpu_saves;
    unsigned cpu_saves_n;
    /* where a callback without a signature lets the guest store an indirect result the
     * native caller gave no buffer for (runtime.c callback_plain): made at first use */
    uint8_t *noret;
    m27_seg *seg;              /* innermost running segment */
    void *eh_landed;           /* exception that stopped at this segment's native-call guard */
    void *eh_resume;           /* exception to resume natively after the current native step */
    m27_eh_rec eh_rec[M27_EH_RECS];
    /* signals (signals.c): a handler that moves pc resumes at the innermost
     * segment's jmp_buf (seg->jb) */
    uint32_t sig_pending;      /* arrived inside a m27 critical section, not yet redelivered */
    uint32_t sig_redeliver;    /* re-raised by m27_crit_leave: use the saved siginfo */
    int in_fault_handler;      /* guest SIGSEGV/SIGBUS handler running (no SA_NODEFER) */
    int sig_depth;             /* segment depth of the innermost guest signal handler running, or 0 */
    int sig_nest;              /* signal handlers tramp/m27_signal_forward are running on this thread */
    /* a native SIGSEGV/SIGBUS handler m27_signal_forward is running without
     * SA_NODEFER (its signal blocked, as natively): the address of a word in
     * the forwarding frame and the token it holds. A handler that left by
     * longjmp leaves them stale; the frame check tells (signals.c). */
    uint64_t nh_at, nh_tok;
    uint32_t nh_blocked;       /* the SIGSEGV/SIGBUS bits such handlers block */
    uint32_t nh_deferred;      /* SIGSEGV/SIGBUS sent (not faults) while such a handler ran: raised after it */
    uint64_t nh_defer_rip;     /* where the last one was deferred */
    siginfo_t sig_info[NSIG];  /* siginfo of each pending signal */
    /* the thread's signal mask blocks SIGSEGV or SIGBUS (set when the state is
     * created, kept by libm27_identity's pthread_sigmask/sigprocmask
     * interposers): m27_probe_read8 must not fault then */
    int sync_masked;
    /* jit.c: the thread's jump cache, one slot per low part of a branch
     * target, and one miss record (two words) per slot behind them (zero
     * until the thread first runs translated code) */
    uint64_t jit_jc[3 * M27_JIT_JC_SLOTS];
    /* runtime.c: the thread's native-target cache (guest branch target -> native target, x1, plan),
     * an entry valid while m27_ncache_gen equals its gen; read and written only by this thread */
    struct { uint64_t pc, gen, target, x1; const struct m27_plan *plan; } ncache[M27_NCACHE_SLOTS];
    /* ncache_owner: 0, or the segment depth (depth) of the cache store in progress on this thread, set and
     * cleared by single stores (a signal handler on top of it neither probes nor stores); ncache_wseq:
     * completed stores, so a probe a handler's store interrupted sees the change */
    int ncache_owner;
    uint32_t ncache_wseq;
    /* runtime.c native_step: the last M27_STEP_RING native calls and traps of this thread (target, lr,
     * x0), a ring the abort report prints (how guest code reached the failing call) */
    struct { uint64_t pc, lr, x0; } step_ring[M27_STEP_RING];
    uint32_t step_n;
} m27_thread;

/* Frames at segment depth `depth` and deeper were abandoned (a nonlocal jump or an exception): a cache
 * store among them never finishes, so its ownership ends. An owner at a smaller depth is a store still
 * suspended below the code that left (runtime.c). */
static inline void m27_ncache_abandon(m27_thread *t, int depth)
{
    int o = __atomic_load_n(&t->ncache_owner, __ATOMIC_RELAXED);
    if (o && o >= depth) __atomic_store_n(&t->ncache_owner, 0, __ATOMIC_RELAXED);
}

/* Where guest execution stands, for a signal that interrupts it. The
 * runtime keeps these in the cpu block's host scratch words. */
#define M27_SCRATCH_IN_GUEST 1  /* host_scratch[1]: 1 while translated code or the interpreter runs */
#define M27_SCRATCH_HELPER   2  /* host_scratch[2]: guest pc of the instruction the interpreter runs */

typedef struct {
    uint64_t gpr[6];
    uint64_t target;
    uint64_t *stack_args;
    uint8_t xmm[8][16];
    uint64_t swift[4];    /* r12 error, r13 self, r14 async context, rax indirect result */
    uint64_t sig;         /* r10: hsig*, tagged Swift recipe, M27_CB_FAULT from NX, else 0 */
    uint64_t ret;         /* return address into the native caller */
    uint64_t rbp;         /* the native caller's rbp at entry */
} m27_cb_frame;
#define M27_CB_FAULT 1
/* r10 of an island entry (swiftmeta.c), whose guest function is a Swift function: swiftcc passes an indirect
 * result's buffer in rax, which m27_callback hands the guest as x8 (a plain C entry's rax is no buffer) */
#define M27_CB_SWIFTCC 4

typedef void (*m27_trap_fn)(m27_thread *t, uint64_t arg);

/* bridge_x86_64.S */
void m27_native_call(uint64_t fn, m27_cpu *cpu, uint64_t nstack);
void m27_callback_entry(void);
/* Call fn(seg) in a frame whose personality is m27_eh_personality; returns
 * 1 when an exception stopped unwinding there, else 0. */
#define M27_GUARD_NATIVE 1     /* around one native call or trap of a segment */
#define M27_GUARD_ENTRY  2     /* around a whole segment */
long m27_eh_guard(void (*fn)(void *), m27_seg *seg, long kind);
uint64_t m27_eh_guard_pad(void);
long m27_raw_syscall(long num, const uint64_t args[9], uint64_t *rdx_out, int *err_out);

/* runtime.c */
m27_thread *m27_current(void);       /* this thread's guest state, created on first use; async-signal-safe */
m27_thread *m27_current_peek(void);  /* the same, or NULL; never allocates */
extern uint64_t m27_threads_live, m27_threads_freed;  /* guest thread states alive / freed at thread exit */
void m27_fork_init(void);              /* register the fork child handler; once, before guest code */
/* m27's own critical sections: every m27 mutex that guest execution can
 * reach (jit_lock, thunk_lock, sig_lock, invoke_lock, the signal table) and
 * m27's allocations on guest paths. A guest signal that arrives on a thread
 * inside one is recorded and re-raised when the outermost section ends
 * (signals.c), so a guest handler never runs on top of a m27 lock or
 * malloc. They nest; m27_crit_depth is async-signal-safe. */
void m27_crit_enter(void);
void m27_crit_leave(void);
int m27_crit_depth(void);
void m27_run(m27_thread *t, const m27_cpu *outer);   /* outer: the caller's saved guest state */
uint64_t m27_call_guest(m27_thread *t, uint64_t fn, const uint64_t *args, int nargs);
uint64_t m27_trap_address(const char *name, m27_trap_fn fn, uint64_t arg);
bool m27_is_trap(uint64_t pc);
void m27_install_fault_handler(void);
/* trust.c: how far the environment is trusted (m27/aot.h); the switches
 * themselves are read once into m27_opt (m27/opts.h, m27_opts_init). */
int m27_env_tier(void);                   /* M27_ENV_OPEN / GUARDED / SETUGID */
/* A set-ID process: dyld expands no @ path and takes no relative dependency
 * path, which m27's own loader follows too (so does a __RESTRICT main
 * executable). dyld_process_is_restricted is not this rule: it also holds
 * for every hardened-runtime program, where dyld does expand @rpath. */
bool m27_env_restricted(void);
/* m27_opts_init for the CLI's stage 2: flags is the inherited M27_FLAGS,
 * argv_flags the letters of m27's own command-line switches, each honoured
 * where the environment switch of the same effect is (trust.c). */
void m27_opts_init_cli(const char *flags, const char *argv_flags);
/* guest_sig.c: code-signature policy for a CLI-form guest, checked on the
 * descriptor the loader then maps (m27_load_main_image); path names the file
 * in diagnostics. 0 = run it; -1 = refused, diagnostic printed. */
int m27_guest_sig_enforce(int fd, const char *path);
/* A main executable whose verified signature s requires library validation:
 * the switches that add code or trust (M27_CACHE, M27_GUEST_ROOT,
 * M27_IF_DIR, M27_ASYNC_CONTRACT_LIBRARY, M27_TRUST_LIST) are cleared, each
 * named on the diagnostics channel. Both front ends call it before the
 * runtime starts (guest_sig.c). */
void m27_guest_switches_for(const m27_codesig *s);
const char *m27_env_tier_name(int tier);
/* Fatal diagnostics: stderr, or the console with boot argument m27_crash=1
 * (daemons' stderr usually goes nowhere and AMFI denies their core dumps),
 * and with m27_crash=1 also /private/var/log/m27/console.log (M27_DIAG_DIR:
 * another directory), which hardware keeps when nothing records the console
 * (runtime.c, "diagnostics channel"). */
#include <stdio.h>
#include "m27/opts.h"
extern FILE *m27_diag_file;
#define M27_DIAG (m27_diag_file ? m27_diag_file : stderr)
void m27_diag_init(void);                 /* once, at startup (m27_install_fault_handler) */
void m27_diag_write(const char *b, size_t n);   /* async-signal-safe: console and log, whole */
size_t m27_diag_fmt_u64(char *out, uint64_t v, unsigned base);   /* async-signal-safe: v in base 10 or 16 */
void m27_diag_write_fatal(const char *b, size_t n);   /* the same for a fatal report: may also open a log still waiting for its volume */
/* Async-signal-safe fatal line: "m27[PROG:PID]: what" and each of n values in hex, through
 * m27_diag_write, then abort (no stdio, no dladdr, no allocation) */
__attribute__((noreturn)) void m27_fatal_safe(const char *what, const uint64_t *v, int n);
extern size_t m27_diag_log_cap;           /* the log's size limit (bytes) */
/* A multi-line report written with one m27_diag_write (m27_diag_block_end):
 * fprintf into m27_diag_block's FILE (M27_DIAG if no memory). Not in signal handlers. */
typedef struct { FILE *f; char *buf; size_t len; } m27_diag_blk;
FILE *m27_diag_block(m27_diag_blk *b);
void m27_diag_block_end(m27_diag_blk *b);
void m27_diag_prefix(FILE *f);            /* "m27[prog:pid]: " */
/* A refusal's public record (runtime.c): "m27-refusal v1 kind=KIND process=\"P\" key=\"K\" reason=\"R\"", once per
 * (KIND, KEY) in a process, as a public unified-log error (subsystem com.m27, category refusal) and a line
 * of m27's diagnostics. Async-signal-safe (fixed buffers, atomics; os_log on a handle made at start-up).
 * KEY and REASON are names and policy text, never addresses or guest data. */
void m27_refusal_record(const char *kind, const char *key, const char *reason);
void m27_refusal_log_init(void);          /* the record's log handle: not in a signal handler */
/* m27's diagnostic line: M27_LOG(level, fmt, ...) writes "m27[prog:pid]: "
 * and the message to M27_DIAG (stderr, or the console and m27_crash's log)
 * in one write, when level <= m27_verbose: 0 always, 1 under -v, 2 under
 * -vv, 3 under -vvv. The arguments are evaluated only then. fmt ends the
 * line itself ("\n"). Not in signal handlers (stdio). */
#define M27_LOG(level, ...) do { if ((level) <= m27_verbose) m27_log_line(__VA_ARGS__); } while (0)
void m27_log_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
/* M27_DIAG_TIME startup phases (runtime.c): mark the front end's start, print exec/front/diag once the
 * diagnostics channel is up, then one line per phase. No-ops without the switch. */
void m27_phase_mark(void);
void m27_phase_begin(void);
void m27_phase(const char *what);
/* m27's own bookkeeping got no memory (`what` names the table): one
 * diagnostic, then abort, never a table that silently lost an entry. Not in
 * signal handlers. m27_alloc_check: p, or m27_oom(what) when p is NULL. */
__attribute__((noreturn, cold)) void m27_oom(const char *what);
static inline void *m27_alloc_check(void *p, const char *what) { if (!p) m27_oom(what); return p; }
void m27_diag_guest_addr(FILE *f, uint64_t a);   /* " (image+off, file vmaddr V)" when a is guest code */
const char *m27_diag_guest_where(uint64_t a, char *buf, size_t n);   /* the same text in buf ("" otherwise) */
uint64_t m27_resolve_import(m27_image *img, int ordinal, const char *name, bool weak, bool *found);
/* Load the main executable from fd, the open descriptor its launcher has
 * checked (main.c); path only names it, entry is the launcher
 * (M27_ENTRY_CLI or M27_ENTRY_EMBEDDED, m27/macho.h). The loader owns fd
 * from here on. m27_main_image is set before its imports bind. */
m27_image *m27_load_main_image(const char *path, int fd, int entry, char *err, size_t errlen);
/* Load every image's remaining dependencies, then run the initialisation of
 * each image not yet initialised, dependencies first (main.c
 * m27_guest_image_init: TLV, ObjC, Swift, blocks, fnslots, AOT, initializers). */
void m27_run_initializers(void);
/* A guest library m27 loads for itself (thunks.c), mapped and bound; NULL with err set. */
m27_image *m27_load_guest_library(const char *path, char *err, size_t n);

/* dsc.c: guest images from the arm64e dyld shared cache */
typedef uint64_t (*m27_dsc_binder)(const char *lib, const char *sym, bool *ok);
bool m27_dsc_has(const char *path);
/* The cache image at path can run as guest code next to native libraries:
 * an arm64 dylib with no ObjC, Swift or thread-local metadata whose every
 * pointer and ADRP+ADD into another cache image names an export (dsc.c).
 * false with the reason in why. */
bool m27_dsc_eligible(const char *path, char *why, size_t n);
m27_image *m27_dsc_load(const char *path, m27_mapped_fn mapped, void *ctx, char *err, size_t errlen);
void m27_dsc_bind(m27_image *img, m27_dsc_binder bind);
/* After m27_dsc_bind: every type_info img exports names native_lib's _ZTS
 * of the same type (one type identity for both copies); the count shared. */
int m27_dsc_share_type_names(m27_image *img, m27_dsc_binder bind, const char *native_lib);
/* A guest branch to cache address pc outside every guest image: *native is
 * where execution continues, and *x1 is nonzero when the target is an
 * objc_msgSend stub, which sets x1 to that native selector first. */
bool m27_dsc_redirect(uint64_t pc, uint64_t *native, uint64_t *x1);
/* pc lies where m27_dsc_redirect looks: a guest cache image is loaded and pc is in the cache */
bool m27_dsc_covers(uint64_t pc);
/* Pre-optimised ObjC metadata of cache images (objc.c): the runtime address
 * of the selector buffer that direct relative method lists are offsets
 * from (0 without one); a pointer-sized field anywhere in the cache, decoded
 * from the slide info when no guest image owns (and so rewrote) it; whether
 * `a` lies in the cache but in no guest image; and making [a, a+n) of a
 * guest cache image writable. */
uint64_t m27_dsc_selector_base(void);
/* The arm64e cache's selector strings (slid; span 0 without a cache): a
 * selector built in code that m27 could not patch is such a string, which is
 * no native SEL. m27_dsc_sel_map returns the native selector for x1 in the
 * range (counted in m27_dsc_sels_mapped), else x1. */
extern uint64_t m27_dsc_sel_lo, m27_dsc_sel_span, m27_dsc_sels_mapped;
uint64_t m27_dsc_sel_map(uint64_t x1);
uint64_t m27_dsc_load_ptr(uint64_t a);
bool m27_dsc_foreign(uint64_t a);
void m27_dsc_writable(uint64_t a, uint64_t n);
/* executable room for Swift metadata island entries within rel32 reach of [lo, hi), carved out of the arm64e
 * cache's reservation (dsc.c); NULL if none */
void *m27_dsc_island(uint64_t lo, uint64_t hi, uint64_t size);
/* What m27_dsc_bind wrote to the pointer-sized cache slot at a (a uniqued
 * GOT slot an image's code or Swift metadata reaches); 0 if it rewrote none. */
uint64_t m27_dsc_bound_ptr(uint64_t a);
/* Bind the cache slot at a as m27_dsc_bind binds a GOT slot (named and bound
 * natively, or slid); true if it is bound now (swiftmeta.c: key path patterns). */
bool m27_dsc_bind_slot(uint64_t a);
extern m27_image *m27_main_image;
extern int m27_verbose;
extern int m27_use_jit;
/* Set by host test drivers that load and register an image without running
 * any of its code: m27_run aborts instead of entering the guest. */
extern bool m27_no_guest_code;

/* objc.c: m27_objc_register_image = m27_objc_read_image (selectors,
 * protocols, classes, categories) + m27_objc_run_loads (+load methods). */
void m27_objc_register_image(m27_image *img);
void m27_objc_read_image(m27_image *img);
void m27_objc_run_loads(m27_image *img);

/* interpose.c */
bool m27_interpose_lookup(const char *name, uint64_t *addr);
int m27_interpose_selfcheck(char *why, size_t n);   /* tests: table order and name indexes; 0 = sound */
/* name is an ABI repair of a native C++ function (applies to native binds only) */
bool m27_interpose_abi_repair(const char *name);
void m27_tlv_setup(m27_image *img);

/* Native entry points the register bridge cannot call exactly have plans
 * (m27/bridge.h, src/bridge.c): imports keep their native address, so
 * native callers and function-pointer comparisons are unaffected; the
 * generic native-call path (m27_run) looks the target's plan up and either
 * extends a narrow result or makes a signature-driven call:
 *   'B' bool, 'c'/'C' signed/unsigned char, 's'/'S' signed/unsigned short:
 *       Apple arm64 callers rely on the callee extending such a result to
 *       32 bits; x86_64 defines only al/ax. Extend x0 after the call.
 *   'A': call through m27_call_native_sig with the plan's encoding
 *       (aggregates by value, arm64 stack arguments packed at their natural
 *       size where SysV uses 8-byte slots, ...). */
const char *m27_stackargs_enc(const char *name);                 /* stackargs_gen.inc signature, or NULL */
/* varsniff.c: bind a variadic native function that no interposer table names */
bool m27_varsniff_bind(const char *name, uint64_t native, uint64_t *addr);
bool m27_varsniff_prologue(const uint8_t *code, int *ngp, int *nfp);
bool m27_varsniff_native(uint64_t fn, int *ngp, int *nfp);   /* checks fn is in an image's __TEXT first */
/* the same, also taking a prologue that saves no GPR as *ngp = 6 (six or more fixed: ObjC IMPs, whose
 * encoding gives the count) */
bool m27_varsniff_native_imp(uint64_t fn, int *ngp, int *nfp);
/* the varsniff bridge for a native variadic fn: fmt = index of the format among the ngp fixed arguments
 * (-1: none), fkind = its SDK family ('p', 'C', ...; 0: typed only as a guest-image constant); returns
 * whether the variadic slots were typed */
bool m27_varsniff_call(struct m27_thread *t, uint64_t fn, int ngp, int nfp, char narrow, int fmt, char fkind, char family);
/* the prologue verdict of a native ObjC IMP, kept per IMP (lock-free reads); decode false in a guest
 * signal handler; *fresh when this call decoded a variadic one */
bool m27_varsniff_imp(uint64_t imp, bool decode, int *ngp, int *nfp, bool *fresh);
bool m27_varsniff_imp_note(uint64_t imp, unsigned bit);   /* one-time reports per IMP (bits 0-5) */
void m27_varsniff_fork_child(void);
/* objc.c: the plan for a native variadic IMP (prologue counts g, f) of a method (enc, sel): false when
 * the encoding disagrees (declined); else the fixed GPR count, the format's index and SDK family */
bool m27_objcva_plan(int g, int f, const char *enc, const char *sel, int *ngp, int *fmt, char *fk);
char m27_narrowret_kind_for_name(const char *name);   /* imported symbol (with '_'); 0 if wide */
char m27_narrowret_kind_for_enc(const char *enc);     /* ObjC/block type encoding's return type */
/* x0 as an Apple arm64 callee leaves it: extended to 32 bits, upper half zero */
static inline uint64_t m27_narrowret_extend(uint64_t v, char kind)
{
    switch (kind) {
    case 'B': case 'C': return (uint8_t)v;
    case 'c': return (uint32_t)(int32_t)(int8_t)v;
    case 's': return (uint32_t)(int32_t)(int16_t)v;
    case 'S': return (uint16_t)v;
    default: return v;
    }
}

/* unwind.c: exceptions and longjmp across native frames */
void m27_eh_landed(m27_thread *t);
void m27_eh_resume_native(m27_thread *t);
void m27_eh_trap_resume(m27_thread *t, uint64_t arg);
void m27_eh_forget(m27_thread *t, int above_depth);
void m27_longjmp_segment(m27_thread *t, int depth);   /* noreturn: re-enter segment `depth` */
/* on_fault: a libunwind personality call to a non-canonical address (the x86_64 libdispatch callout CIE)
 * returns _URC_FATAL_PHASE1_ERROR, as the arm64e noexcept personality does; init before on_fault is installed */
void m27_eh_fault_init(void);
bool m27_eh_fault(ucontext_t *uc);
/* rip is the instruction after a `callq *%rax` in libunwind's __TEXT: where m27_eh_fault leaves a context */
bool m27_eh_fault_target(uint64_t rip);
extern uint64_t m27_eh_badpers_hits;

/* syscall.c */
void m27_guest_svc(m27_thread *t);

/* signals.c */
void m27_signals_init(void);
int m27_signal_raw_sigaction(int sig, const void *nsa, void *osa);   /* a guest svc sigaction(2) */
void m27_signals_stats(void);
void m27_signals_fork_child(void);   /* the disposition lock */
void m27_vafmt_fork_child(void);     /* vafmt.c: constant formats being made */
bool m27_signal_forward(int sig, siginfo_t *info, void *uc);
/* on_fault gives up on a fault: SIG_DFL, the signal and SIGILL/SIGTRAP unblocked (also in the
 * context the kernel restores), a sent signal raised again: the default action is certain */
void m27_signal_giveup(int sig, const siginfo_t *info, ucontext_t *uc);
/* the kernel's sigaction, past libm27_identity's interposers (m27's own dispositions) */
int m27_sys_sigaction(int sig, const struct sigaction *act, struct sigaction *oact);
void m27_signals_hook(void);           /* native sigaction of SIGSEGV/SIGBUS -> the shadow; after on_fault is installed */
extern uint64_t m27_sig_native_installs;   /* native installs of SIGSEGV/SIGBUS kept in the shadow */
/* bridge_x86_64.S: fn(sig, info, uctx); bit mask of callee-saved registers it changed (1 rbx, 4 r12, ...) */
int m27_sig_call_native(uint64_t fn, int sig, siginfo_t *info, void *uctx);
void m27_signal_redeliver(m27_thread *t, uint32_t pending);
void m27_signals_block_async(void);    /* on m27's own threads: no asynchronous signal is delivered there */
/* Leave on_fault without __sigreturn and without any system call: restore every register ctx's mcontext holds
 * and jump to its rip (never returns; the mask is already the ucontext's), or return when it must go back
 * through the kernel (switch off, Rosetta, alternate stack, a state or rip outside what it resumes).
 * path: what on_fault did, for the counts. */
enum { M27_RESUME_NX, M27_RESUME_TRAP, M27_RESUME_COMMPAGE, M27_RESUME_STALE, M27_RESUME_GIVEUP, M27_RESUME_EHPERS, M27_RESUME_PROBE, M27_RESUME_NPATHS };
void m27_sig_resume(void *ctx, int path);
/* bridge_x86_64.S: *out = the 8 bytes at a, or false when the read faults (on_fault resumes at the fail
 * label). Only where m27_probe_safe(t) holds. */
bool m27_probe_read8(uint64_t a, uint64_t *out);
extern char m27_probe_read8_load[], m27_probe_read8_fail[];
/* t may take a fault in m27_probe_read8: not inside a signal handler m27 runs (tramp, m27_signal_forward)
 * and SIGSEGV/SIGBUS not blocked, as far as libm27_identity's mask interposers tell */
bool m27_probe_safe(const m27_thread *t);
/* a may be read by m27_probe_read8: not in the main thread's stack reservation below its stack, where
 * xnu turns a protection fault into SIGSEGV with SIG_DFL forced (handle_ux_exception) */
bool m27_probe_addr_ok(uint64_t a);
/* Nonempty, nonoverflowing word spans: aligned, or one unaligned word wholly
 * within one page. Same guarded reader and VM-generation-aware unreadable-page
 * cache as block argument scans; both unaligned endpoints check stack guards. */
bool m27_probe_read_words(const m27_thread *t, uint64_t a, uint64_t *out, unsigned n);
extern uint64_t m27_probe_faults;     /* m27_probe_read8 reads that faulted */
/* libm27_identity's count of calls that can map memory or make it readable (src/blocks.c read_words), or NULL */
extern const uint64_t *m27_vm_gen;
/* a fetch fault at addr whose context stands at _sigtramp's entry (another signal's frame came first) */
bool m27_sig_stale(const ucontext_t *uc, uint64_t addr);
/* a data fault at addr whose context stands at _sigtramp's entry and whose interrupted context stands at pc */
bool m27_sig_stale_at(const ucontext_t *uc, uint64_t addr, uint64_t pc);
void m27_sig_resume_print(FILE *f);    /* the "m27: signal returns:" line (--stats, m27_nx_stats) */
size_t m27_sig_resume_line(char *b, size_t cap);   /* the same line into b (async-signal-safe); 0: none */
/* SIG's default action now, from inside a handler: SIG_DFL, SIG unblocked, raised; never returns */
__attribute__((noreturn)) void m27_signal_die(int sig);
/* the shadow handler of SIGSEGV/SIGBUS (what m27_signal_forward would call), else 0 */
uint64_t m27_signal_shadow_handler(int sig);
/* The last action m27's signal handlers (on_fault, tramp) took on this thread, kept for the failed-__sigreturn
 * report (runtime.c on_abort). uctx/tok: the delivery's ucontext and sigreturn token (tok 0: not known). */
enum { M27_ACT_NX = 1, M27_ACT_NX_TRAP, M27_ACT_COMMPAGE, M27_ACT_FORWARD, M27_ACT_GIVEUP, M27_ACT_TRAMP, M27_ACT_STALE, M27_ACT_EHPERS, M27_ACT_PROBE };
void m27_fault_act_note(int kind, int sig, uint64_t a, uint64_t b, const void *uctx, uint64_t tok);
/* bridge_x86_64.S: the kernel's handlers for on_fault and on_abort, fn(sig, info, uctx), which call
 * m27_on_fault/m27_on_abort(sig, info, uctx, r12): r12 is the sigreturn token _sigtramp keeps there */
void m27_fault_entry(int sig, siginfo_t *info, void *uctx);
void m27_abort_entry(int sig, siginfo_t *info, void *uctx);

/* jit.c */
int m27_jit_run_block(m27_thread *t);   /* returns exit reason */
void *m27_jit_translate(uint64_t pc);   /* host tests: translate, do not run; NULL: interpreted */
/* The guest instruction a host pc inside translated code belongs to:
 * false if host_pc is not in a translated block. *flags_in_host: the guest
 * NZCV lives in host RFLAGS there, not in cpu.flags. */
bool m27_jit_locate(uint64_t host_pc, uint64_t *guest_pc, bool *flags_in_host);
bool m27_jit_init(void);      /* false: no executable memory (interpreter, no thunks) */
bool m27_have_thunks(void);
/* Fixed cache-relative data pool, RW/non-executable, owned for the process lifetime. */
#define M27_ASYNC_RECORD_SLOTS 8192u
#define M27_ASYNC_RECORD_SIZE 16u
void *m27_jit_async_records(void);
bool m27_thunk_range(uint64_t *lo, uint64_t *hi); /* entire reserved plain-thunk area */
void m27_jit_stats(void);
/* `m27 --aot [--cache DIR] [--cpu=SPEC] FILE`: write FILE's AOT cache into cache_dir (absolute), or with
 * NULL into the directory this process writes to, translated for at most the CPU features cpu
 * (m27/cpufeat.h; m27_cpu_profile_parse); 0 or -1 (reported) */
int m27_aot_generate_file(const char *path, bool verbose, const char *cache_dir, uint64_t cpu);
int m27_aot_check_file(const char *path, const char *cache_dir, bool print, uint64_t cpu);
bool m27_aot_write_dir(char *out, size_t n);   /* the directory `m27 --aot` with no --cache writes to; false: none */
bool m27_aot_generation(uint8_t out[16]);
uint64_t m27_thunk_for(uint64_t guest);
uint64_t m27_swift_thunk_for(uint64_t guest);   /* passed to a native Swift function: entry marked M27_CB_SWIFTCC */
uint64_t m27_thunk_target(uint64_t thunk);
bool m27_in_code_cache(uint64_t a);
void m27_jit_alias(uint64_t entry, uint64_t guest);
uint64_t m27_sig_thunk(uint64_t guest, void *sig);   /* sig: an hsig* (abi.c); 0 if none can be made */
uint64_t m27_sig_thunk_target(uint64_t thunk);
void m27_jit_fork_child(void);         /* fork child: re-initialise the module's mutexes */
/* W^X (jit.c): m27's executable memory (code cache, Swift islands) is read/execute only. m27_wx_region
 * makes MAP_JIT memory mapped read/write (not executable) such a region; a write maps the bytes it
 * changes read/write elsewhere for its duration: m27_wx_begin (NULL: frozen, a fork child on an Intel
 * Mac) .. m27_wx_end, in a m27 critical section, no write inside another. */
typedef struct { uintptr_t x, map, len; } m27_wx_win;
bool m27_wx_region(void *x, size_t size);
bool m27_wx_covers(uint64_t a, uint64_t n);
/* interpose.c: a guest call that would change the mapping (map: map over, unmap) or the protection
 * (prot) of [a, a+n) where it overlaps m27's executable memory, the thunks' table or (map, or prot with
 * write or execute) an image's sealed constant data: refused (EACCES / KERN_PROTECTION_FAILURE) */
bool m27_guest_vm_guarded(uint64_t a, uint64_t n, int prot, bool map);
void *m27_wx_begin(void *x, size_t n, m27_wx_win *win);
void m27_wx_end(m27_wx_win *win, size_t written);
bool m27_wx_write(void *x, const void *src, size_t n);
/* blocks.c */
void m27_thunk_native_args(m27_thread *t);
void m27_thunk_native_blocks(m27_thread *t);
void m27_thunk_native_code_slots(m27_thread *t, unsigned slots);   /* typed slots (m27_sig_msgsend) */
void m27_blocks_register_image(m27_image *img);
bool m27_blocks_heal(uint64_t a0, uint64_t target);
void m27_blocks_stats(void);
void m27_blocks_fork_child(void);
void m27_dsc_fork_child(void);
/* gss.c: GSS-API calls and data with x86_64 pack(2) layouts (gss_gen.inc) */
bool m27_gss_interpose(const char *name, uint64_t *addr);
void m27_gss_fork_child(void);
void m27_objc_fork_child(void);      /* objc.c: the deferred-category lock, type strings being made */
/* objc.c: categories deferred to Swift-runtime realization, attached since, classes realized so */
void m27_objc_late_stats(uint64_t *deferred, uint64_t *attached, uint64_t *classes);
/* swiftcc.c: x86_64 swifttailcc argument area (callee pop) of a guest async
 * function from its own code, or -1 when it cannot be analysed */
int64_t m27_swift_async_arg_area(uint64_t fn, const char **why);
/* Whether m27 gave TYPE's native metadata the arm64 layout and verified it
 * (swift.c: Swift.Float16's 2-byte POD witnesses). */
bool m27_swift_relaid_type(const void *type);
/* its x86_64 stack-slot count (-1: not analysable), whether the arm64 pop at
 * an exit set it, and the instruction that did (offline checks) */
int m27_swift_async_slots(uint64_t fn, int *from_pop, uint64_t *at);
/* Narrow results of native Swift async functions reach the guest as an
 * argument of its continuation: kinds 'b' Bool, 'h'/'H' Int8/UInt8,
 * 't'/'T' Int16/UInt16 (the kind of the native entry's plan). */
char m27_swift_async_result_kind(const char *sym);           /* "_$s...": 0 if not async or wide */
char m27_swift_async_kind_for_demangled(const char *d);
void m27_swift_async_note_call(uint64_t ctx, char kind);     /* guest calls such a function, x22 = its context */
uint64_t m27_swift_async_extend_result(uint64_t target, uint64_t x0);   /* native resumes guest `target` */
void m27_swiftcc_fork_child(void);
/* Native generic results (swiftcc.c): a native method or getter whose result is
 * G?, G a generic parameter of Self's type, gets the outer nils it makes in
 * guest storage in the guest's encoding. bind: at binding (true: the plan
 * gets M27_PLAN_GENRESULT); call: at each guest call, 0 or the address to
 * convert with *type after a synchronous call returns (an async call is
 * converted when its callee resumes the guest); result: that conversion. */
bool m27_swift_genres_bind(uint64_t native, const char *sym, bool is_async);
bool m27_swift_genres_demangled(const char *d, char *context, size_t n, int *param, bool *throwing);
uint64_t m27_swift_genres_call(const m27_cpu *c, uint64_t native, const void **type);
void m27_swift_genres_result(const void *type, uint64_t result);
/* Native View modifiers (swiftcc.c): a native protocol extension method of
 * SwiftUI.View lends self (x20) to native witnesses with Self's nils in the
 * native encoding. bind: at binding (true: the plan gets M27_PLAN_VIEWSELF);
 * call: at each guest call, NULL or a converted copy of self to pass in x20
 * for the call (then freed). */
bool m27_swift_viewself_bind(uint64_t native, const char *sym);
void *m27_swift_viewself_call(const m27_cpu *c, uint64_t native);
/* Packed stack arguments of a native Swift function without a layout record
 * (swiftcc.c; plan kind M27_KIND_SITEPACK): the layout the guest call site
 * returning to c->x[30] stored for its call of native, as m27_ifunit_stackpack
 * gives one (at most M27_IF_MAX_PACK entries), or 0 when the word copy is
 * right or unproven. */
int m27_swift_sitepack(const m27_cpu *c, uint64_t native, uint8_t *off, uint8_t *width);
void m27_swift_note_dispatch_thunk(const char *gname, uint64_t native);
int m27_swift_callee_stack_slots(uint64_t native);   /* SysV stack arguments past x6, x7 its code reads; -1 not shown */
/* interpose.c: a page the guest mapped or made executable itself (mmap,
 * mprotect): arm64 code generated at run time, which m27 does not run */
bool m27_guest_exec_mapped(uint64_t a);
void m27_guest_vm_note(uint64_t a, uint64_t n, bool exec);   /* the guest mapped [a, a+n): executable or not */
void m27_interpose_fork_child(void);
static inline bool m27_swift_async_kind(char k) { return k == 'b' || k == 'h' || k == 'H' || k == 't' || k == 'T'; }
/* fnslots.c */
void m27_fnslots_image(m27_image *img);
void m27_fnslots_stats(void);
/* taken[k] = 1 for each fn[k] (sorted, distinct) whose address the image's code materializes */
void m27_fnslots_address_taken(m27_image *img, const uint64_t *fn, size_t nfn, uint8_t *taken);
/* swiftmeta.c: relative pointers in Swift metadata to guest functions ->
 * thunks in islands within rel32 range of the image */
/* a layout string a guest metadata instantiation function hands to swift_cvw_instantiateLayoutString: its
 * enum-tag and resilient-accessor functions get island entries (once per string) */
void m27_swiftmeta_layout_template(uint64_t ls);
void m27_swiftmeta_image(m27_image *img);
/* The ObjC class stub a non-generic Swift class descriptor in a guest image names
 * (TargetObjCResilientClassStubInfo: a class with resilient ancestry), else 0. */
uint64_t m27_swift_class_stub(uint64_t descriptor);
void m27_swiftmeta_stats(void);
/* The relative indirect(able) references of an image's Swift metadata whose
 * slot lies outside the image, by kind (m27_swiftmeta_slots, swiftmeta.c). */
enum {
    M27_SK_TYPEREF_CTX, M27_SK_TYPEREF_CONF, M27_SK_TYPEREF_ASSOC, M27_SK_TYPEREF_OBJCPROTO,
    M27_SK_TYPE_RECORD, M27_SK_PROTO_RECORD, M27_SK_PARENT,
    M27_SK_REQ_PROTO, M27_SK_REQ_OBJCPROTO, M27_SK_REQ_CONF,
    M27_SK_CONF_PROTO, M27_SK_CONF_TYPEREF, M27_SK_CONF_OBJCCLASS, M27_SK_CONF_RETRO, M27_SK_CONF_WITNESS,
    M27_SK_CONF_GLOBALACTOR, M27_SK_SUPERCLASS, M27_SK_OVERRIDE_CLASS, M27_SK_OVERRIDE_METHOD,
    M27_SK_DEFOVER_REPL, M27_SK_DEFOVER_ORIG, M27_SK_PATTERN_VWT, M27_SK_SHAPE_VWT,
    M27_SK_REPLACE_KEY, M27_SK_REPLACE2_ORIG, M27_SK_N
};
typedef void (*m27_swift_slot_fn)(void *ctx, int kind, uint64_t field, uint64_t slot);
size_t m27_swiftmeta_slots(m27_image *img, m27_swift_slot_fn fn, void *ctx);
const char *m27_swiftmeta_slot_kind(int kind);
bool m27_swiftmeta_is_island(uint64_t a);
uint64_t m27_swiftmeta_island_target(uint64_t entry);   /* guest function of an island entry, else 0 */
bool m27_swiftmeta_interpose(const char *name, uint64_t *addr);   /* swift_continuation_init, swift_task_create, ... */
void m27_swiftmeta_resume_parent(uint64_t ctx);
void m27_async_contract_prepare(m27_cpu *, uint64_t target, const char *symbol);
void m27_async_contract_fork_child(void);
void m27_swiftmeta_async_args(m27_cpu *c);   /* async function records in x0-x7 for a native Swift function (M27_PLAN_ASYNCARG) */
bool m27_swift_async_param(const char *sym);   /* a Swift function with a parameter of async function type (swiftcc.c) */
/* "_$s...": a type metadata accessor or a value witness, whose signature the metadata ABI fixes, or a `dynamic`
 * function prologue's runtime call (swiftcc.c) */
bool m27_swift_fixed_abi(const char *sym);
bool m27_swift_async_param_demangled(const char *d);
/* NSTextAlignment (arm64 Center 1 Right 2, x86_64 the reverse) in a native Swift function's signature:
 * bits 0-7 argument registers x0-x7, M27_SWIFT_ENUM_RESULT the result (swiftcc.c) */
#define M27_SWIFT_ENUM_RESULT 0x100
#define M27_SWIFT_ENUM_PENDING 0x8000   /* decided again at the first call (an Objective-C class not yet registered) */
uint16_t m27_swift_textalign(const char *sym, bool final);
uint16_t m27_swift_textalign_demangled(const char *d, bool final);
uint64_t m27_swift_textalign_value(uint64_t v);   /* the other architecture's value (an involution) */
void m27_swiftmeta_fork_child(void);   /* an AsyncContext handed to native code: thunk its ResumeParent */
/* callbacks.c */
bool m27_callbacks_interpose(const char *name, uint64_t *addr);
/* libm.c: <fenv.h> with the arm64 values; long double functions bound to their double twins */
bool m27_libm_interpose(const char *name, uint64_t *addr);
void m27_callbacks_stats(void);
/* abi.c */
bool m27_sig_simple(const char *enc);
/* Stable parsed identity by encoding content (or NULL), never descriptor address. */
const void *m27_sig_identity(const char *enc);
/* every argument of enc an integer, pointer, float or double and the result one too (or void): their
 * GPR and FP counts (self and _cmd included); false otherwise */
bool m27_sig_scalar_args(const char *enc, int *ngp, int *nfp);
bool m27_call_native_sig(m27_cpu *c, uint64_t fn, const char *enc);
/* an ObjC method encoding for objc_msgSend to a native IMP: true when the register bridge is exact
 * (m27_sig_simple); *codeslots: the arm64 argument slots holding code ('^?' function pointers, '@?'
 * blocks, such fields of by-value aggregates; m27/bridge.h argmask bits), *fnret: a '^?' result */
bool m27_sig_msgsend(const char *enc, unsigned *codeslots, bool *fnret);
bool m27_sig_native_memret(const char *enc);
uint64_t m27_guest_entry_for_sig(uint64_t guest, const char *enc);
/* Explicit API callback contract: only supported unambiguous simple void
 * signatures receive a typed entry; everything else keeps a plain thunk. */
uint64_t m27_guest_entry_for_void_sig(uint64_t guest, const char *enc);
void m27_abi_fork_child(void);
void *m27_guest_sig(uint64_t guest);   /* signature noted for a guest function that has no signature thunk */
/* native entry point (plain or signature thunk, Swift metadata island entry) -> guest function, else 0 */
static inline uint64_t m27_guest_of_entry(uint64_t a)
{
    uint64_t g = m27_thunk_target(a);
    if (!g) g = m27_sig_thunk_target(a);
    return g ? g : m27_swiftmeta_island_target(a);
}
bool m27_aot_load(m27_image *im);
/* AOT use records (M27_AOT_USE, aot_use.c): set up once before the first image is initialised; one record per image */
void m27_aot_use_init(void);
void m27_aot_use_image(m27_image *im);
/* phys_footprint and its limit (launchd's jetsam limit), bytes; false: no limit or unreadable (jit.c) */
bool m27_footprint_limit(uint64_t *footprint, uint64_t *limit);

/* Closure fields discovered from Swift allocation metadata. */
void m27_swift_object_prepare(uint64_t object);
void m27_objc_prepare_generic_class(uint64_t metadata);
void m27_swift_objects_args(m27_thread *t);
/* a value of struct or tuple TYPE guest code made, lent to native Swift code: its closure fields get Swift entries */
void m27_swift_value_closures(uint64_t type, uint64_t value);
/* swiftcc.c: the Body a guest body witness returned in native memory (A: its x0-x5): its closures get Swift entries */
void m27_swift_body_result(uint64_t target, const uint64_t *a, uint64_t value);
void m27_swift_objects_result(m27_thread *t, uint64_t target, uint64_t indirect);
bool m27_swift_objects_interpose(const char *name, uint64_t *addr);
/* valist_builder.c: Swift's __VaListBuilder with the arm64 semantics guest code inlines */
bool m27_valist_builder_interpose(const char *name, uint64_t *addr);
extern uint64_t m27_vb_metadata_calls, m27_vb_va_lists, m27_vb_deallocs, m27_vb_destroyed;
void m27_swift_objects_fork_child(void);
void m27_swift_objects_stats(void);

#endif
