/*
 * m27 — the typed guest/native boundary (docs/architecture.md).
 *
 * Every native entry point that guest code calls gets one plan, decided when
 * the import binds (m27_bridge_bind, from runtime.c native_sym) or, for an
 * address the guest obtained some other way (dlsym, a native function
 * pointer in native data), on its first call (dladdr names it). A plan has
 * exactly one class:
 *
 *   EXACT    the register bridge is proven correct for this signature
 *            (an interface unit says so): direct call.
 *   TYPED    a runtime signature or a generated thunk pair exists: a
 *            narrow result to extend, an aggregate signature for abi.c,
 *            a Swift async narrow result, a native block's signature.
 *   REFUSED  an object whose layout differs between the architectures would
 *            cross and the exporter cannot become guest code, or a required
 *            generated adapter is unavailable: diagnostic trap.
 *   UNKNOWN  no authoritative type: the raw bridge with the full argument
 *            scan (x0-x7 and 8 stack slots), counted per symbol
 *            (m27_bridge_stats) and named on its first call by
 *            M27_BRIDGE_CHECK=1.
 *
 * A bare address that names a function m27 interposes (printf, setjmp,
 * ...: the import would have bound to a trap) gets a plan that redirects
 * the call to that trap, so a function pointer from dlsym behaves as the
 * import does.
 *
 * Plans come from two sources, merged at bind time:
 *   - interface units: one file per native library, keyed by the x86_64
 *     library's LC_UUID (/usr/lib/m27/if/<uuid>, or an absolute M27_IF_DIR),
 *     trusted like AOT caches (src/aot_policy.c) and validated by
 *     m27_ifunit_check; a library whose UUID has no unit stays UNKNOWN;
 *   - the tables compiled into libm27 (stackargs_gen, sigdb_gen with
 *     aggspi_gen, narrowret_gen; Swift async narrow results by demangling;
 *     Swift type metadata accessors and value witnesses by demangling, EXACT;
 *     native block signatures seen at run time). Interposers (variadics,
 *     varsniff, setjmp, ...) bind the import to a trap and are counted as
 *     TYPED binds; they never reach the plan table.
 *
 * Plans are keyed by code identity: the defining x86_64 image and the
 * function's offset from its mach header (the plan table by native address).
 * Names are labels. A unit's records resolve to addresses when the unit is
 * loaded (the unit is keyed by its library's LC_UUID, so a name resolves to
 * exactly one function there: one walk of the library's export trie), and the
 * trap table's and compiled-in tables' names resolve through every loaded
 * image's trie (re-exports through dyld). A function reached under any name
 * (a renamed re-export such as CoreGraphics' _CGSX -> SkyLight's _SLSX, an
 * alias at the same address, a dlsym'd or stub-slot address) gets the records
 * of every name its address has: the import's own name first, then the
 * others; two names typed differently is REFUSED. A function an interposer
 * answers under one name is answered by its trap under every name
 * (m27_bridge_trap_at). A unit record under a name the library does not
 * export at an address is found by that name only.
 *
 * The argument mask (argmask) says which argument slots may carry guest code
 * (a function pointer, a block, a byref, void *, id): bits 0-7 are x0-x7,
 * bits 8-15 the first 8 stack argument slots. Only a plan with
 * M27_PLAN_MASKED has an authoritative mask; every other plan gets the full
 * scan, as before typed plans existed.
 *
 * Generated thunk pairs (src/thunks.c; the generator is not part of this repository): a TYPED plan
 * with thunk_guest set is called through its pair instead of the register
 * bridge. m27_run continues at thunk_guest (an arm64e function with the
 * callee's own prototype, lowered by clang for AAPCS64) with the guest's
 * registers, stack and lr unchanged and x18 = &tp (Apple arm64 reserves x18:
 * no compiled code allocates it). The guest half stores its arguments into an
 * array of 8-byte slots (aggregates by address) and calls tp[1], the native
 * half (x86_64, the same prototype lowered by clang for SysV), which loads
 * them, converts the leaves whose layout differs, gives guest code among them
 * native entry points (tp[2] = m27_bridge_code_arg) and calls tp[0], the
 * native function. The native half's own plan is EXACT with an empty mask.
 */
#ifndef M27_BRIDGE_H
#define M27_BRIDGE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "m27/aot.h"
#include "m27/swift_reverse.h"

/* Visit direct regular exports from the original mapped image's complete trie.
 * False means absent/malformed export information; no negative proof is valid. */
typedef void (*m27_native_export_visitor)(void *, const char *, const void *);
bool m27_bridge_native_exports(const void *address, m27_native_export_visitor visit, void *context);

enum { M27_BR_UNKNOWN = 0, M27_BR_EXACT = 1, M27_BR_TYPED = 2, M27_BR_REFUSED = 3, M27_BR_NCLASS = 4 };

/* Where a plan's type came from. */
enum {
    M27_BRS_NONE,          /* nothing: UNKNOWN */
    M27_BRS_UNIT,          /* an interface unit record */
    M27_BRS_STACKARGS,     /* stackargs_gen.inc */
    M27_BRS_SIGDB,         /* sigdb_gen.inc, aggspi_gen.inc and the hand entries in interpose.c */
    M27_BRS_NARROWRET,     /* narrowret_gen.inc */
    M27_BRS_SWIFTASYNC,    /* a Swift async function's narrow result, from its mangled name */
    M27_BRS_BLOCKSIG,      /* a native block's signature (blocks.c m27_note_native_block) */
    M27_BRS_TRAP,          /* an interposer bound the import to a trap */
    M27_BRS_VARSNIFF,      /* varsniff.c bound an untabled variadic to a trap */
    M27_BRS_THISRET,       /* a C++ constructor or non-deleting destructor, from its mangled name (kind 'P') */
    M27_BRS_THUNK,         /* the native half of a generated thunk pair (src/thunks.c) */
    M27_BRS_SWIFTABI,      /* a Swift type metadata accessor or value witness, by its mangled name, or a dynamic
                            * replacement lookup (swiftcc.c m27_swift_fixed_abi): EXACT, no argument is guest
                            * code to be called */
    M27_BRS_OBJCIMP,       /* a native ObjC method's IMP called bare: its method's encoding, found from the
                            * call's receiver and selector (objc.c m27_objc_imp_types) */
    M27_BRS_CXXSTR,        /* cxxstr_gen.inc: std::string parameters converted (kind 'Z') */
    M27_BRS_N
};

#define M27_BR_SCAN_ALL 0xffffu       /* argmask: x0-x7 (bits 0-7), stack slots 0-7 (bits 8-15) */

/* plan flags */
#define M27_PLAN_MASKED  0x01         /* argmask is authoritative (scan only the slots it names) */
#define M27_PLAN_DLADDR  0x02         /* bound on the first call of a bare address */
#define M27_PLAN_SWIFTASYNC 0x04      /* a Swift async function (its library exports the async function pointer
                                       * NAME + "Tu"): x22 is the callee's AsyncContext (swiftmeta.c) */
#define M27_PLAN_ASYNCARG 0x08        /* a Swift function with a parameter of async function type (its demangled
                                       * name): async function records in x0-x7 get m27's copy (swiftmeta.c) */
#define M27_PLAN_PROVEN 0x10          /* unit type justified by the bounded code prover */
#define M27_PLAN_GENRESULT 0x40       /* a native generic result G? (swiftcc.c m27_swift_genres_bind) */
#define M27_PLAN_VIEWSELF 0x80        /* a native SwiftUI.View modifier: self lent converted (swiftcc.c m27_swift_viewself_bind) */
#define M27_PLAN_PROBE  0x20          /* an untyped bare address: later calls may still find its ObjC type
                                       * (a receiver or block the first call did not carry) */
/* resmask (for W6: native function pointers and blocks the guest receives) */
#define M27_RES_CODE     0x01         /* the result may be a native function pointer */
#define M27_RES_BLOCK    0x02         /* the result may be a native block */

typedef struct m27_plan {
    uint64_t native;                  /* the native entry point */
    const char *name;                 /* Mach-O symbol name ('_'...), NULL when nobody needs it */
    const char *enc;                  /* arm64 type encoding for m27_call_native_sig (kind 'A') */
    const char *why;                  /* REFUSED/UNKNOWN reason from a unit, or NULL */
    const void *thunk_guest, *thunk_native;   /* generated thunk pair (src/thunks.c), or NULL */
    uint64_t tp[3];                   /* x18 for the guest half: {native, thunk_native, m27_bridge_code_arg} */
    uint64_t redirect;                /* a trap (m27_trap_address) that answers this entry point: an
                                       * interposed function reached through a bare address (dlsym) */
    uint64_t calls;                   /* counted only with m27_bridge_stats or M27_BRIDGE_CHECK */
    uint16_t argmask;
    uint8_t cls, src, flags, resmask;
    char kind;                        /* 'A' (abi.c with enc), 'B' 'c' 'C' 's' 'S' (narrow result),
                                       * 'b' 'h' 'H' 't' 'T' (Swift async narrow result),
                                       * 'P' (returns its this argument on arm64, void on x86_64),
                                       * 'K' (Swift packed stack arguments: enc is the layout,
                                       * m27_ifunit_stackpack),
                                       * 'Z' (std::string parameters converted, roles in enc: src/cxxstr.c),
                                       * 'D' (Foundation.Data? nils converted, roles in enc: m27_optdata_enc_ok),
                                       * 'k' (UNKNOWN synchronous Swift function: packed stack arguments from the
                                       * call site, M27_KIND_SITEPACK), 0 */
    uint8_t reported;                 /* M27_BRIDGE_CHECK printed its first-call line */
    uint16_t swift_enum;              /* NSTextAlignment registers converted (m27_swift_textalign): bits 0-7
                                       * arguments x0-x7, M27_SWIFT_ENUM_RESULT the result */
    uint8_t probes;                   /* M27_PLAN_PROBE: calls that looked for an ObjC type */
    uint8_t swiftfn;                  /* 1: bound under a Swift mangled name (a guest function passed to it is a
                                       * Swift function value); kept by every later plan of the address */
    uint64_t sel;                     /* M27_BRS_OBJCIMP: the selector the method was found by */
    uint64_t bsig;                    /* M27_BRS_BLOCKSIG: the typing block's signature string when it lies in
                                       * the shared cache (never freed), else 0 (m27_bridge_block_recheck) */
} m27_plan;

/* The plan table: open addressing keyed by native address, grown by
 * replacement (a new, larger table is published; old ones stay valid for
 * readers that still hold them). Reads take no lock and never allocate, so
 * they are safe in signal handlers. */
typedef struct { uint64_t key; const m27_plan *plan; } m27_plan_slot;
typedef struct m27_plan_table {
    uint32_t mask, shift, used, limit;
    struct m27_plan_table *prev;
    uint64_t pad;
    m27_plan_slot s[];
} m27_plan_table;
extern m27_plan_table *m27_plan_tab;

static inline const m27_plan *m27_bridge_lookup(uint64_t a)
{
    const m27_plan_table *t = __atomic_load_n(&m27_plan_tab, __ATOMIC_ACQUIRE);
    if (!t || !a) return NULL;
    uint32_t h = (uint32_t)((a * 0x9E3779B97F4A7C15ull) >> t->shift);
    for (;;) {
        uint64_t k = __atomic_load_n(&t->s[h].key, __ATOMIC_ACQUIRE);
        if (k == a) return __atomic_load_n(&t->s[h].plan, __ATOMIC_ACQUIRE);
        if (!k) return NULL;
        h = (h + 1) & t->mask;
    }
}

/* Calls of an untyped bare address (M27_PLAN_PROBE) that look again for its ObjC type: a
 * method's first call may come with a nil receiver, say. */
#define M27_PLAN_OBJC_PROBES 8

/* m27_bridge_plan_for_call(t, p->native) returns p itself, with no side effect, for as long as
 * the table holds p (it may be remembered per call target: runtime.c's native-target cache) */
static inline bool m27_bridge_plan_settled(const struct m27_plan *p)
{
    return p && (!(p->flags & M27_PLAN_PROBE) || __atomic_load_n(&p->probes, __ATOMIC_RELAXED) >= M27_PLAN_OBJC_PROBES);
}

/* Counting is on with m27_bridge_stats or M27_BRIDGE_CHECK (m27_bridge_init). */
extern bool m27_bridge_counting;

struct m27_thread;
void m27_bridge_init(void);           /* once, after m27_opts_init */
/* Import resolution: guest_name is the import ('_'...), native_name the
 * symbol the x86_64 library exports (a variant may differ), native its
 * address. Records the plan; returns its class. */
int m27_bridge_bind(const char *guest_name, const char *native_name, uint64_t native);
/* An import an interposer or varsniff bound to a trap (src M27_BRS_TRAP/VARSNIFF). */
void m27_bridge_bind_trap(const char *name, int src);
/* A native entry point typed at run time (a native block's invoke, ...):
 * becomes TYPED unless it already has a typed plan. */
void m27_bridge_note(uint64_t native, char kind, const char *enc, int src);
/* The plan kind an ObjC method or block encoding needs on a guest -> native
 * call: 'A' (abi.c, when the encoding is not register-exact), a narrow
 * result kind, or 0 (the register bridge is exact). */
char m27_bridge_kind_for_enc(const char *enc);
/* Native code whose type the ObjC runtime records, called bare by guest code
 * (an IMP from class_getMethodImplementation, method_getImplementation,
 * -methodForSelector: or any other route, a native block's invoke): the
 * encoding from the call itself. objc.c: the method at or above x0's class
 * whose IMP for selector x1 is imp; blocks.c: the signature of the native
 * block in x0 whose invoke is inv. Reads are guarded (mach_vm_read_overwrite);
 * NULL when the registers do not show one. */
const char *m27_objc_imp_types(uint64_t self, uint64_t sel, uint64_t imp);
/* AppKit's TARGET_ABI_USES_IOS_VALUES enums at typed method boundaries.
 * callback=true verifies a native -> guest method target; zero target is a
 * resolved objc_msgSend. The returned token describes the result conversion. */
struct m27_cpu;
unsigned m27_objc_enum_args(struct m27_cpu *c, uint64_t target, bool callback);
void m27_objc_enum_result(struct m27_cpu *c, unsigned token);
/* NSUnarchiver type strings whose BOOL codes were passed as x86_64's, and ones with a 'B' left as
 * they were (not in a guest image, or the table full) */
extern uint64_t m27_objc_bool_strings, m27_objc_bool_unconverted;
const char *m27_native_block_sig(uint64_t a, uint64_t inv);
/* A native IMP the runtime handed back detached from its method (the old IMP
 * of class_replaceMethod, method_setImplementation): typed now by enc. */
void m27_bridge_note_imp(uint64_t imp, const char *enc, uint64_t sel);
/* objc.c: a native variadic IMP's trap (bridged as objc_msgSend bridges its method), else 0.
 * decode false: only an IMP already decoded (a guest signal handler runs). */
uint64_t m27_objc_variadic_imp_trap(uint64_t imp, bool decode);
/* A call through an ObjC-typed IMP plan with another selector than it was
 * typed by: the plan to use for this call (p, or tmp filled for it). */
struct m27_cpu;
const m27_plan *m27_bridge_objc_recheck(const struct m27_cpu *c, const m27_plan *p, m27_plan *tmp);
/* A call through a plan typed by a native block's signature (M27_BRS_BLOCKSIG): the plan to use
 * for the block in x0 (p, or tmp filled from that block's own signature). */
struct m27_thread;
const m27_plan *m27_bridge_block_recheck(struct m27_thread *t, const m27_plan *p, m27_plan *tmp);
/* blocks.c: the signature address of the native block at a whose invoke is inv, read as the argument
 * scan reads (0: no such block, or no signature); whether a lies in the shared cache (never freed) */
uint64_t m27_native_block_sigptr(const struct m27_thread *t, uint64_t a, uint64_t inv);
bool m27_native_static_addr(uint64_t a);
/* The plan for a guest -> native call of pc: the table, else (outside
 * signal handlers) a first-call bind by dladdr. NULL: UNKNOWN, unnamed. */
const m27_plan *m27_bridge_plan_for_call(struct m27_thread *t, uint64_t pc);
/* Per-call bookkeeping with counting on (stats, M27_BRIDGE_CHECK lines). */
void m27_bridge_count(struct m27_thread *t, const m27_plan *p, uint64_t pc);
/* M27_BRIDGE_CHECK: the full scan would have changed a slot the mask skips. */
void m27_bridge_check_mask(struct m27_thread *t, const m27_plan *p);
/* A call to a REFUSED native function: diagnostic, then abort. */
__attribute__((noreturn)) void m27_bridge_refused(struct m27_thread *t, const m27_plan *p);
void m27_bridge_stats(void);          /* the per-process report (stderr or the console) */
/* A guest signal handler is running on this thread: no dladdr, no first-call decoding. */
bool m27_bridge_in_signal_handler(struct m27_thread *t);
/* objc.c: native variadic methods sent through objc_msgSend (varsniff verdict per IMP): IMPs found,
 * calls bridged, calls whose slots no format typed, and IMPs whose encoding disagreed (not bridged) */
extern uint64_t m27_objcva_imps, m27_objcva_calls, m27_objcva_untyped, m27_objcva_declined;
void m27_bridge_fork_child(void);
void m27_bridge_stats_atexit(void);
/* Every name an interposer answers with a trap (interpose.c and the modules it
 * asks: runtime, signals, callbacks, swiftobjects, gss, libm, swiftmeta,
 * objc, regvar), and every name of the compiled-in tables (m27_tables_plan):
 * cb(name, ctx) for each; names are static strings. */
typedef void (*m27_name_fn)(const char *name, void *ctx);
void m27_interpose_names(m27_name_fn cb, void *ctx);
void m27_tables_names(m27_name_fn cb, void *ctx);
/* A name-sorted index over a static table whose entries begin with their
 * name (const char *), built at its first use (interpose.c). m27_name_find
 * answers a name with the table's first entry of that name, as a linear scan
 * would, or -1; m27_name_each lists the same names in table order, so an
 * interposer whose lookup and names both go through one index cannot
 * answer a name it does not list. keep (may be NULL) leaves entries out of
 * both. Every index that has been used is checked by
 * m27_interpose_selfcheck. */
typedef struct m27_name_index {
    const void *base;
    size_t stride, n;
    bool (*keep)(const void *e);
    uint32_t *idx;                 /* NULL until built */
    size_t len;
    struct m27_name_index *next;   /* the built indexes */
} m27_name_index;
#define M27_NAME_INDEX(t, keep) {(t), sizeof *(t), sizeof (t) / sizeof *(t), (keep), NULL, 0, NULL}
long m27_name_find(m27_name_index *ix, const char *name);
void m27_name_each(m27_name_index *ix, m27_name_fn cb, void *ctx);
const char *m27_name_at(const m27_name_index *ix, size_t k);   /* entry k's name */
/* The call plans that still read a demangler's spelling (swiftcc.c: Swift
 * async narrow results and async parameters; cxxstr.c: std::string-family
 * parameters) checked against fixed symbols: 0 as expected, n > 0 mismatches
 * (the first in why), -1 when that demangler is not loaded. m27 -v reports a
 * mismatch; tests/host/bridge.c requires none. */
int m27_swift_demangle_selfcheck(char *why, size_t n);
int m27_cxx_demangle_selfcheck(char *why, size_t n);
/* The runtime's own interposer (runtime.c: __dyld_lazy_load). */
bool m27_runtime_interpose(const char *name, uint64_t *addr);
void m27_runtime_interpose_names(m27_name_fn cb, void *ctx);
void m27_signal_interpose_names(m27_name_fn cb, void *ctx);
void m27_callbacks_interpose_names(m27_name_fn cb, void *ctx);
void m27_swift_objects_interpose_names(m27_name_fn cb, void *ctx);
void m27_valist_builder_interpose_names(m27_name_fn cb, void *ctx);
void m27_gss_interpose_names(m27_name_fn cb, void *ctx);
void m27_libm_interpose_names(m27_name_fn cb, void *ctx);
bool m27_ioreg_interpose(const char *name, uint64_t *addr);
void m27_ioreg_interpose_names(m27_name_fn cb, void *ctx);
void m27_swiftmeta_interpose_names(m27_name_fn cb, void *ctx);
void m27_objc_interpose_names(m27_name_fn cb, void *ctx);
void m27_regvar_interpose_names(m27_name_fn cb, void *ctx);
/* The trap that answers the native function at `native` under one of its other
 * names (runtime.c native_sym binds the import to it): 1 with *trap, 0 for
 * none, -1 when two of its names have different traps (the plan is REFUSED). */
int m27_bridge_trap_at(uint64_t native, uint64_t *trap);
/* interpose.c: the compiled-in tables by guest import name. Returns the
 * source (M27_BRS_STACKARGS, _SIGDB, _NARROWRET) with *kind ('A' with *enc,
 * or a narrow kind), or 0. */
int m27_tables_plan(const char *name, char *kind, const char **enc);
/* name ('_'...) is a C++ constructor (C1, C2, CI1, CI2) or non-deleting
 * destructor (D1, D2): Apple arm64 returns `this` from it in x0 (the ARM C++
 * ABI that Apple arm64 follows; clang's AppleARM64CXXABI HasThisReturn),
 * x86_64 returns void. */
bool m27_cxx_this_return(const char *name);
/* ObjC BOOL is bool on arm64 and signed char on x86_64 (objc.h
 * OBJC_BOOL_IS_BOOL), so a C++ function with BOOL in its signature has two
 * mangled names: b in the arm64 slice, a in the x86_64 one. The offsets in
 * name ("__Z...", a function's encoding) of the builtin type b: their count,
 * or -1 for data, special and local names, what the walk does not read and
 * more than M27_CXX_BOOL_MAX. */
#define M27_CXX_BOOL_MAX 6
int m27_cxx_bool_types(const char *name, size_t off[M27_CXX_BOOL_MAX]);
/* blocks.c: the argument scan. m27_thunk_native_args_mask scans the slots
 * mask names (M27_BR_SCAN_ALL: m27_thunk_native_args); m27_args_code_slots
 * reports, changing nothing, which slots the full scan would act on. */
void m27_thunk_native_args_mask(struct m27_thread *t, unsigned mask, bool swiftcc);   /* swiftcc: a Swift callee */
void m27_thunk_native_stack_swift(struct m27_thread *t, const uint16_t *off, int n, unsigned mask);   /* a Swift callee's stack words */
unsigned m27_args_code_slots(struct m27_thread *t);

/* cxxstr.c: native C++ functions outside libc++ that take a std::string
 * (src/cxxstr_gen.inc). A kind 'Z' plan's enc is 'P' (the arm64 function
 * returns this: a constructor or destructor) or '.', then one role per
 * integer argument register x0..x5: '-' none, 'r' const std::string&,
 * 'v' std::string by value (by address on both ABIs), 'w' std::string&
 * (written back). At least one role. */
static inline bool m27_cxxstr_enc_ok(const char *e)
{
    if (!e || (e[0] != 'P' && e[0] != '.')) return false;
    size_t n = 0, roles = 0;
    for (const char *p = e + 1; *p; p++, n++) {
        if (*p != '-' && *p != 'r' && *p != 'v' && *p != 'w') return false;
        if (*p != '-') roles++;
    }
    return n >= 1 && n <= 6 && roles;
}
/* Foundation.Data? (Optional<Data>) passed or returned directly by a native
 * Swift function (plan kind 'D', from an interface unit). A live Data has the
 * same 16 bytes on both architectures (its tag is the top two bits of byte 15:
 * 00 inline, 01 slice, 11 empty); only the Optional's nil differs:
 * (0, 0xf000000000000000) on arm64, (0, 0xff00000000000000) on x86_64. The
 * arm64 nil read by x86_64 code is a live empty Data, so a nil argument
 * arrives as "present, 0 bytes". enc is 'R' (the result in x0/x1 is a
 * Data? too) or '.', then one role per integer argument register x0..x7:
 * 'd' the first word of a Data? argument, '+' its second word (always next),
 * '-' anything else (trailing '-' may be left out: 'R' alone is a result-only
 * function). At least one Data?. Only exact nil words are rewritten;
 * live values pass unchanged. */
#define M27_OPTDATA_NIL_ARM UINT64_C(0xf000000000000000)
#define M27_OPTDATA_NIL_X86 UINT64_C(0xff00000000000000)
static inline bool m27_optdata_enc_ok(const char *e)
{
    if (!e || (e[0] != 'R' && e[0] != '.')) return false;
    size_t n = 0, pairs = e[0] == 'R';
    for (const char *p = e + 1; *p; p++, n++) {
        if (*p == 'd') { if (p[1] != '+') return false; pairs++; p++; n++; }
        else if (*p != '-') return false;
    }
    return n <= 8 && pairs;
}
struct m27_cpu;
/* The call (kind 'Z'): guest strings converted to native ones around it. */
void m27_cxxstr_call(struct m27_cpu *c, uint64_t pc, const char *enc);
/* The generated table by name: 1 typed (*enc), -1 REFUSED (*why), 0 not listed. */
int m27_cxxstr_lookup(const char *name, const char **enc, const char **why);
/* the table's names (m27_tables_names: they resolve by address, src/bridge.c labels_at) */
void m27_cxxstr_names(m27_name_fn cb, void *ctx);
/* A native C++ function outside libc++ whose signature names a type whose
 * layout differs (std::string, std::function, string streams): the reason to
 * refuse it, or NULL. lib: the defining image's path. */
const char *m27_cxxstr_divergent(const char *name, const char *lib);

/* thunks.c: generated thunk pairs. m27_thunks_init loads the thunk
 * libraries of the interface-unit directory (before the main image, so
 * before library validation applies: src/libval.c); m27_thunks_pair finds a
 * pair by key (false: none, the plan stays without one). */
void m27_thunks_init(void);
bool m27_thunks_pair(const char *key, uint64_t *guest_half, uint64_t *native_half);
void m27_thunks_stats(void);
/* Resolve by symbol and library UUID, not the address-keyed forward plan:
 * the linker may fold distinct dispatch functions with different signatures. */
const m27_swift_reverse *m27_bridge_swift_requirement(uint64_t requirement,
    const m27_swift_result_signature **result);
/* A native half's guest-code argument: a guest function gets its native
 * entry (enc: its arm64 type encoding when it needs a signature thunk, or
 * NULL), a guest block or byref gets its helpers thunked (blocks.c). */
uint64_t m27_bridge_code_arg(uint64_t a, const char *enc);
/* bridge.c: the plan of a native half (EXACT, empty mask) */
void m27_bridge_note_native_half(uint64_t nh);

/* ----------------------------------------------- interface units (pure)
 * file = hdr | recs[nrecs] | strtab[strtab_size]
 * Records are sorted by name (strcmp, strictly increasing). strtab[0] and
 * its last byte are NUL; string fields are strtab offsets, 0 = none.
 * payload_hash = m27_aot_hash64(recs..end of strtab). Integrity only;
 * trust comes from ownership (m27_aot_open_trusted). */
#define M27_IF_MAGIC "M27IF01"
#define M27_IF_VERSION 2
#define M27_IF_MAX_FILE (64ull << 20)
#define M27_IF_MAX_RECS (1u << 20)
#define M27_IF_SYS_DIR "/usr/lib/m27/if"

typedef struct {
    char magic[8];
    uint32_t version, hdr_size;
    uint8_t uuid[16];                 /* LC_UUID of the x86_64 library the unit describes */
    uint32_t nrecs, rec_size;
    uint32_t strtab_off, strtab_size;
    uint64_t file_size, payload_hash;
} m27_if_hdr;

/* record flags */
#define M27_IFR_MASKED 0x01           /* argmask is authoritative */
#define M27_IFR_SIG    0x02           /* call through abi.c with enc (kind 'A') */
#define M27_IFR_THUNK  0x04           /* call through the generated thunk pair named by thunk
                                       * (_m27_gh_KEY / _m27_nh_KEY in a thunk library); with
                                       * M27_IFR_SIG too, enc is the abi.c equivalent (M27_NO_THUNKS,
                                       * the differential check) */
#define M27_IFR_PROVEN 0x08           /* versioned scalar code proof, not a source prototype */
#define M27_IFR_FLAGS  (M27_IFR_MASKED | M27_IFR_SIG | M27_IFR_THUNK | M27_IFR_PROVEN)
/* Versioned code proofs. Earlier scalar proofs (scalar-leaf-v1,
 * scalar-call-v1/v2) accepted arguments used as memory: their units are rejected and must be regenerated. */
#define M27_IF_PROOF_PREFIX "source=code: proof=scalar-call-v3;"
/* Result-only evidence never authorizes an argument mask or an EXACT claim. */
#define M27_IF_RESULT_PROOF_PREFIX "source=code: proof=result-only-v1;"
#define M27_IF_MAX_KEY 64

typedef struct {
    uint32_t name, enc, why, thunk;   /* strtab offsets; thunk: the pair's key ([A-Za-z0-9_]) */
    uint16_t argmask;
    uint8_t cls, kind, flags, resmask;
    uint16_t rsv;                     /* 0 */
} m27_if_rec;

typedef struct {
    const m27_if_hdr *hdr;
    const m27_if_rec *recs;
    const char *str;
    uint32_t nrecs, str_size;
} m27_if_view;

/* Validate a whole unit held in memory (4-byte aligned). uuid: the library
 * it must describe (NULL: any). 0 = valid, v filled in; -1 = rejected with
 * the reason in why. Never reads outside buf[0..size). */
int m27_ifunit_check(const void *buf, uint64_t size, const uint8_t *uuid, m27_if_view *v, char *why, size_t n);
/* Record kind 'K' (generated outside this repository): a Swift function whose Apple arm64 stack arguments
 * are packed at natural size. enc "OFF.W,OFF.W,..." names, for SysV slots 2, 3, ... (x6 and x7
 * take slots 0 and 1), the arm64 byte offset and width of the argument each carries. Returns the
 * number of slots (offsets and widths filled in), or -1 for an invalid layout. */
#define M27_IF_KIND_STACKPACK 'K'
/* Plan kind (never a unit record's): an UNKNOWN synchronous native Swift function, whose packed stack
 * arguments, if any, come from the guest call site (swiftcc.c m27_swift_sitepack). */
#define M27_KIND_SITEPACK 'k'
#define M27_IF_MAX_PACK 32
int m27_ifunit_stackpack(const char *enc, uint8_t off[M27_IF_MAX_PACK], uint8_t width[M27_IF_MAX_PACK]);
/* The record for a symbol name in a validated unit, or NULL. */
const m27_if_rec *m27_ifunit_find(const m27_if_view *v, const char *name);
/* The directory interface units load from; returns 1, or 0 (none, with
 * the reason in why). An absolute if_dir (M27_IF_DIR, honoured in the OPEN
 * tier only, kind M27_AOT_EXPLICIT) replaces the system directory
 * M27_IF_SYS_DIR (kind M27_AOT_SYS_RO, root-owned). */
int m27_if_dirs_for(int tier, const char *if_dir, m27_aot_dir *out, char *why, size_t n);

#endif
