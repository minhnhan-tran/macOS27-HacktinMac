/*
 * m27 — arm64/arm64e Mach-O image loader for the hybrid model.
 *
 * Guest images are mapped into the host (x86_64) process. Their code pages
 * are mapped readable but NOT executable: translated code reads them as
 * data, and a native call into guest code faults, which the runtime turns
 * into a guest callback. Imports are bound through a resolver supplied by
 * the runtime, normally to native x86_64 library symbols.
 */
#ifndef M27_MACHO_H
#define M27_MACHO_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>

#define M27_MAX_DEPS 256
#define M27_MAX_RANGES 16
#define M27_MAX_RPATHS 32
#define M27_MAX_LAZY 32

typedef struct m27_image m27_image;

/* Resolve an import. ordinal: 1..n = dependency index+1, 0 = self,
 * -1 = main executable, -2 = flat, -3 = weak lookup.
 * Return the address to store; set *found=false if unresolved. name is
 * valid for the call only (it lies in the image's fixups, which the loader
 * gives back when the load ends): a resolver that keeps it copies it. */
typedef uint64_t (*m27_resolver)(m27_image *img, int ordinal, const char *name, bool weak, bool *found);

struct m27_image {
    char path[1024];
    int fd;                     /* open only while loading (-1 after) */
    uint64_t dev, ino;          /* the file loaded (st_dev, st_ino) */
    uint64_t slice_off, slice_size;
    uint32_t cpusubtype;
    uint64_t base;              /* runtime address of the mach header */
    uint64_t pref_base;         /* __TEXT vmaddr */
    uint64_t slide;
    uint64_t vmsize;
    /* Exact mapped segments for native address-to-image queries. Immutable once
     * listed, retained with the descriptor after unload; excludes reserved holes
     * and the shared cache's ambiguous common __LINKEDIT. */
    uint64_t (*identity_ranges)[2];
    uint32_t n_identity_ranges;
    /* a shared-cache image (dsc.c): its segments lie apart in the cache, so
     * [base, base+vmsize) is only __TEXT and every segment is listed here */
    bool dsc;
    uint64_t seg_lo[M27_MAX_RANGES], seg_hi[M27_MAX_RANGES];
    int nseg;
    uint64_t entry;             /* LC_MAIN entry (runtime) or 0 */
    uint64_t stack_size;
    bool is_dylib, is_pie;
    uint64_t code_lo[M27_MAX_RANGES], code_hi[M27_MAX_RANGES];
    int ncode;
    /* SG_READ_ONLY segments (__DATA_CONST, __AUTH_CONST) made read-only by m27_image_seal; m27's
     * own later writes into them go through m27_image_sealed_write */
    uint64_t sealed_lo[M27_MAX_RANGES], sealed_hi[M27_MAX_RANGES];
    int nsealed;
    /* where m27 reads the code ranges (m27/code.h): guest address + code_delta (0: the guest
     * mapping itself); code_alias/code_alias_size: an alias this image owns (on-disk images) */
    int64_t code_delta;
    uint64_t code_alias, code_alias_size;
    int ndeps;
    char *deps[M27_MAX_DEPS];
    bool dep_weak[M27_MAX_DEPS];      /* LC_LOAD_WEAK_DYLIB */
    bool dep_reexport[M27_MAX_DEPS];  /* LC_REEXPORT_DYLIB */
    bool dep_upward[M27_MAX_DEPS];    /* LC_LOAD_UPWARD_DYLIB */
    bool dep_cxx[M27_MAX_DEPS];       /* an import from it has an Itanium C++ name (__Z...) */
    void *dep_handle[M27_MAX_DEPS];   /* runtime.c: native handle or a marker */
    struct m27_image *dep_image[M27_MAX_DEPS];  /* runtime.c: the guest image of a guest dependency */
    char *rpaths[M27_MAX_RPATHS];     /* LC_RPATH, unexpanded */
    int nrpaths;
    /* LC_LAZY_LOAD_DYLIB_INFO blobs (one per lazily loaded dylib) */
    uint8_t *lazy_info[M27_MAX_LAZY];
    uint32_t lazy_info_size[M27_MAX_LAZY];
    int nlazy;
    struct m27_image *loader;   /* runtime.c: the image whose dependency loaded this one (@rpath search chain) */
    int init_state;             /* runtime.c: 0 loaded, 1 initialising, 2 initialised */
    uint32_t load_seq;          /* load order (main executable first) */
    /* initializers */
    uint64_t init_ptrs, init_ptrs_count;     /* S_MOD_INIT_FUNC_POINTERS */
    uint64_t init_offs, init_offs_count;     /* S_INIT_FUNC_OFFSETS (u32 offsets) */
    /* thread-local variables */
    uint64_t tlv_desc, tlv_desc_count;       /* S_THREAD_LOCAL_VARIABLES: {thunk,key,offset} */
    uint64_t tlv_init_start, tlv_init_size;  /* template: REGULAR then ZEROFILL */
    uint64_t tlv_total_size;
    /* linkedit */
    const uint8_t *exports_trie;
    uint32_t exports_trie_size;
    /* all sections (for ObjC/Swift metadata) */
    struct { char seg[17], sect[17]; uint64_t addr, size; uint32_t flags; } sects[128];
    int nsects;
    /* fixup statistics */
    uint64_t n_rebase, n_bind, n_auth, n_unresolved;
    uint8_t uuid[16];
    bool has_uuid;
    char libval_route[96];      /* libval.c: how its code signature was decided ("" not checked) */
    /* the CodeDirectory its pages were verified with (libval.c m27_slice_verify), on the bytes it was built
     * from: its hash type (0: the image was not verified) and cdhash */
    uint8_t cdhash_type, cdhash[20];
    uint64_t func_starts_off;   /* LC_FUNCTION_STARTS (file offset in slice) */
    uint32_t func_starts_size;
    uint64_t *fstarts;          /* sorted absolute function start addresses */
    size_t nfstarts;
    uint64_t *fnslots;          /* chained-fixup rebase slots whose target is a guest function start */
    size_t nfnslots, capfnslots;
    void *jit_slot;             /* code-cache word holding `base` for PIC code */
    void *aot;                  /* loaded AOT cache in the code cache (jit.c aot_publish) */
    uint64_t aot_size, aot_cpu; /* its size and the CPU profile it was translated under (m27/aot.h cpu_profile) */
    void *aot_lazy;             /* its lazy install record (jit.c aot_lazy), or NULL when installed whole */
    uint8_t aot_state;          /* what m27_aot_load found (M27_AOT_STATE_*, m27/aot.h): AOT use records */
    uint64_t jit_blocks;        /* blocks of this image the JIT translated (jit.c emit_block, atomic) */
    /* executable memory for the image's Swift metadata island entries (swiftmeta.c), mapped right after the
     * image when it was reserved (m27_island_reserve): [isl_lo, isl_hi), bump-allocated from isl_next */
    uint64_t isl_lo, isl_hi, isl_next;
    bool released;              /* taken off m27_images (read atomically by the lookups) */
    struct m27_image *next;     /* m27_images: see m27_image_register */
};

/* Island entries (swiftmeta.c) are rel32 targets: they must lie within +-2 GB of the fields that name them.
 * A process can fill that range of its image with later mappings before the image's metadata is walked
 * (WindowManager, Finder, sharingd and searchpartyd on MacBookPro9,2 do), so when m27_island_reserve is set the
 * loader maps an image with Swift metadata together with executable room after it, while the room is free:
 * one 32-byte entry per possible function start plus M27_ISLAND_DYN_SLOTS for entries made at run time. */
#define M27_ISLAND_SLOT 32
#define M27_ISLAND_DYN_SLOTS 1024
extern bool m27_island_reserve;
/* the bytes of island room for an image with nfstarts_bound function starts at most */
uint64_t m27_island_room(uint64_t nfstarts_bound);

/*
 * The slice of a guest file that an image is built from, as bytes that do
 * not change while m27 uses them. m27 checks a guest's code signature itself
 * (libval.c, guest_sig.c) and builds the image afterwards. If the two read
 * the file separately, whoever may write the file can change it in between:
 * the kernel validates a native image page by page as it pages it in, and
 * nothing does that for pages m27 maps as data. So the slice is taken once,
 * and the mach header, the load commands, the signature, every hashed page,
 * the linkedit blobs and the segments all come from bytes:
 *  - copied: every file not on the read-only System volume. The slice was
 *    read once into anonymous memory, and the image's segments are made of
 *    that memory (whole pages are handed over, not copied again). The image
 *    then shares no page with the file or with another process: it costs
 *    the slice's size for as long as it is loaded, in memory that can be
 *    compressed but not dropped (measured: 7.2 MB more peak footprint for an
 *    8.4 MB library, 66 MB for a 67 MB one; load time unchanged);
 *  - not copied: a file on the volume the system booted from, mounted
 *    read-only (the System volume, sealed or a snapshot), that root owns
 *    with no group or other write bit and no ACL. bytes is the file,
 *    mapped, and the segments are mapped from the file as before, shared
 *    with every process that maps it.
 * fd stays the file for what the kernel is asked about it (F_ADDFILESIGS_INFO,
 * F_CHECK_LV); the slice does not own it.
 */
/* An embedded code signature as m27_codesig_parse (libval.c) read it: the
 * one reader, for the CLI's policy, integrity and library validation alike.
 * Pointers lie in the signature that was parsed (for a slice: in its bytes). */
typedef struct {
    const uint8_t *cd;          /* the CodeDirectory of highest hash rank (NULL: nothing parsed) */
    uint32_t cd_len;
    uint32_t flags;             /* its flags (adhoc 0x2, library 0x2000, runtime 0x10000) */
    uint8_t platform;           /* platform identifier (0: not a platform binary) */
    uint8_t hash_type, hash_size, page_shift;
    uint32_t hash_off, n_code_slots;
    uint64_t code_limit;
    uint8_t cdhash[20];
    const char *ident, *team;   /* NUL-terminated in cd; team NULL: no Team ID */
    const uint8_t *ents;        /* XML entitlements blob, header included, sealed by cd (NULL: none) */
    uint32_t ents_len;
    const uint8_t *der_ents;    /* DER entitlements blob, likewise */
    uint32_t der_ents_len;
    bool dlv;                   /* com.apple.security.cs.disable-library-validation */
} m27_codesig;

typedef struct m27_slice {
    int fd;
    uint64_t dev, ino;          /* the file (st_dev, st_ino) */
    uint64_t off, size;         /* the slice in the file */
    const uint8_t *bytes;       /* the slice, [0, size) */
    bool copied;
    uint32_t cpusubtype;        /* the mach header's */
    uint32_t filetype, flags;   /* the mach header's */
    const uint8_t *cmds;        /* the load commands, in bytes, validated (m27_macho_check_cmds) */
    uint32_t ncmds, sizeofcmds;
    int nsegs;                  /* LC_SEGMENT_64 commands */
    uint32_t sig_off, sig_size; /* its one LC_CODE_SIGNATURE (sig_size 0: not signed); not yet checked to lie in the slice */
    m27_codesig sig;            /* the signature, once m27_slice_verify accepted it (sig.cd NULL: not verified) */
} m27_slice;
/* Page-hash passes m27_slice_verify has made in this process (tests: one for each signed image). */
extern unsigned m27_slice_verify_passes;
/* The slice the loader runs (arm64e, else arm64) of the regular file open on
 * fd; NULL with why filled if there is none or its header or load commands
 * are malformed. */
m27_slice *m27_slice_open(int fd, char *why, size_t n);
void m27_slice_close(m27_slice *sl);
/* Leave sl for the loader: the next m27_load_image_fd_cb of sl->fd builds the
 * image from it instead of opening the slice again, and closes it. For a
 * caller that has checked these bytes and must load what it checked
 * (m27_guest_sig_enforce). */
void m27_slice_hand(m27_slice *sl);
/* The loader's unit tests: 1 maps every file, 2 copies every file (0: maps
 * only a root-owned file without write bits or ACL on the read-only volume
 * the system booted from). */
extern int m27_slice_test_path;
/* The rule m27_slice_open maps a file by: owner uid, mode, its volume's
 * statfs flags, and whether the file has no ACL (the lookup said ENOENT). */
bool m27_slice_fixed_rule(uid_t uid, mode_t mode, uint32_t mnt_flags, bool acl_none);

/* Load path (fat or thin), choose arm64e (else arm64) slice, map, apply
 * fixups. The file is untrusted: every offset, size and count is validated
 * against the slice, its linkedit blobs and the reserved region. Returns
 * NULL and fills err (naming the offending field) on failure. */
m27_image *m27_load_image(const char *path, m27_resolver resolve, char *err, size_t errlen);
/* The same, calling mapped(img, ctx) once the image is mapped and listed in
 * m27_images, before any of its imports are resolved (lets a loader record
 * the image first, so that a cyclic dependency finds it). */
typedef void (*m27_mapped_fn)(m27_image *img, void *ctx);
m27_image *m27_load_image_cb(const char *path, m27_resolver resolve, m27_mapped_fn mapped, void *ctx, char *err,
                             size_t errlen);
/* The same from fd, an open descriptor of the file, instead of opening path
 * (which then only names the image): for a caller that has checked that
 * descriptor and must load what it checked (the slice it left with
 * m27_slice_hand, if any). The image owns fd from here on; it is closed
 * whether the load succeeds or fails. */
m27_image *m27_load_image_fd_cb(const char *path, int fd, m27_resolver resolve, m27_mapped_fn mapped, void *ctx,
                                char *err, size_t errlen);
/* Native bundle fallback: recheck ARM-only MH_BUNDLE on the descriptor used
 * for signature validation and mapping, not only the preflight pathname. */
bool m27_is_arm_only_bundle(const char *path);
bool m27_is_arm_loadable(const char *path, bool dylib);        /* includes universal files; routing, not validation */
bool m27_is_arm_only_loadable(const char *path, bool dylib);   /* an ARM-only MH_BUNDLE, or with dylib also an MH_DYLIB */
/* universal: a guest-owned app library; otherwise recheck the ARM-only fallback on the open descriptor. */
m27_image *m27_load_dlopen_cb(const char *path, bool universal, m27_resolver resolve, m27_mapped_fn mapped, void *ctx, char *err, size_t errlen);
m27_image *m27_load_bundle_cb(const char *path, m27_resolver resolve, m27_mapped_fn mapped, void *ctx,
                              char *err, size_t errlen);
/* The generation of the native-target caches (runtime.c run_loop): what a guest branch to a
 * non-guest address resolves to (shared-cache redirect, x1, bridge plan) is remembered per thread
 * until the next call of m27_ncache_invalidate, which whatever can change that answer makes after
 * the change is visible: an image registered or released (here), a plan inserted or replaced
 * (bridge.c tab_put). */
extern uint64_t m27_ncache_gen;   /* src/macho.c */
static inline void m27_ncache_invalidate(void) { __atomic_add_fetch(&m27_ncache_gen, 1, __ATOMIC_SEQ_CST); }
/* Add an image built elsewhere (dsc.c) to m27_images, in load order. */
void m27_image_register(m27_image *img);
/* Map only (no fixups, no binding): for offline translation. */
m27_image *m27_map_image_only(const char *path, char *err, size_t errlen);

/* Take an image off m27_images and unmap it (failed loads, fuzzing). A
 * listed image's struct and the arrays a lookup reaches stay allocated:
 * another thread may hold it from a lookup that raced the release. */
void m27_unload_image(m27_image *img);
/* After every adaptation of a loaded image (its registration, before its initializers), as dyld
 * does: its SG_READ_ONLY segments become read-only. On-disk images only. False if a segment could
 * not be sealed (the others are; a later call seals only what is left). */
bool m27_image_seal(m27_image *img);
/* Does [a, a+n) overlap a sealed segment of any image */
bool m27_image_sealed_overlap(uint64_t a, uint64_t n);
/* The image whose sealed segments hold the n bytes at a, or NULL */
m27_image *m27_image_sealed_at(uint64_t a, size_t n);
/* Compare-and-swap of the 8 bytes at a in a sealed segment: writable for the store only (one such
 * store at a time), read-only again after it; a page that cannot be made read-only again stops the
 * process. */
bool m27_image_sealed_cas(uint64_t a, uint64_t expected, uint64_t desired);
/* Validate a load-command area (cmdsize, per-command sizes, nsects, string
 * offsets, at most one LC_CODE_SIGNATURE); 0 if every command is well
 * formed, else -1 with why filled. *nsegs gets the LC_SEGMENT_64 count.
 * Used for files and cache images. */
int m27_macho_check_cmds(const uint8_t *cmds, uint32_t sizeofcmds, uint32_t ncmds, int *nsegs, char *why, size_t n);
/* The file's arm64 dylib slice has no ObjC or Swift metadata (a C++
 * library that may run as guest code next to its native slice); false with
 * the reason in why. */
bool m27_macho_guest_cxx_eligible(const char *path, char *why, size_t whylen);
/* The slice the loader runs from a file of fsize bytes: arm64e (plain
 * subtype first), else arm64. Thin files give off 0 and size fsize. */
bool m27_macho_choose_slice(int fd, uint64_t fsize, uint64_t *off, uint64_t *size, uint32_t *sub, char *why, size_t whylen);

/* The code signature of a slice and library validation as the guest gets it
 * natively (src/libval.c). */
/* Parse an embedded signature (SuperBlob) as xnu validates one; false with
 * why filled if it is malformed or has no CodeDirectory. */
bool m27_codesig_parse(const uint8_t *blob, size_t len, m27_codesig *cs, char *why, size_t n);
/* The cdhash and hash type of an embedded signature held in memory, after
 * m27_codesig_parse's structure checks (no page is hashed): the AOT loader's
 * code identity of a signed image (jit.c). */
bool m27_guest_sig_cdhash(const uint8_t *blob, size_t len, uint8_t cdhash[20], uint8_t *hash_type, char *why, size_t n);
/* Would the program load only platform or same-team libraries natively? */
bool m27_codesig_library_validated(const m27_codesig *cs);
/* Check the slice's signature on its bytes: structure, every page hash, and
 * that nothing in the slice but the signature lies outside the hashed range.
 * 0: valid, and sl->sig is the signature; -1: not (why; *bad_exec, if not
 * NULL, when a page of an executable segment does not match its hash). A
 * slice is hashed once: a second call for a verified slice returns 0. */
int m27_slice_verify(m27_slice *sl, bool *bad_exec, char *why, size_t n);
/* Who signed a guest library whose signature m27 verified (sl->sig), where
 * the kernel cannot say (guest_sig.c m27_guest_sig_image, through
 * m27_libval_set_verifier): nobody (ad hoc), Apple (a trusted cdhash list
 * or `anchor apple`), or with team a certificate of that Team ID. */
typedef struct {
    bool apple;          /* a trusted cdhash list or `anchor apple` (Security) */
    bool team_ok;        /* asked with a team: the certificate is that Team ID's */
    const char *by;      /* who vouched */
} m27_imgsig;
typedef int (*m27_imgsig_fn)(const m27_slice *sl, const char *path, const char *team, m27_imgsig *out, char *why, size_t n);
int m27_guest_sig_image(const m27_slice *sl, const char *path, const char *team, m27_imgsig *out, char *why, size_t n);
/* The runtime hands libval.c the signer check (guest_sig.c), and whether to
 * establish signers in user space even where the kernel could
 * (M27_LIBVAL_USER, tests). Without one (the loader's unit tests and
 * fuzzers) a library that needs it is refused. report writes a diagnostic
 * line (the diagnostics log) before an invalid page ends the process. */
void m27_libval_set_verifier(m27_imgsig_fn fn, bool force_user, void (*report)(const char *line));
/* An embedded program's stub names the ARM slice it was made for in its
 * __TEXT,__m27_slice section (tools/m27-embed writes it; the stub's own
 * signature, which the kernel validates, covers it): the hash type and
 * cdhash of that slice's CodeDirectory. */
#define M27_SLICE_SECTION "__m27_slice"
typedef struct {
    char magic[8];              /* M27_SLICE_MAGIC */
    uint8_t version;            /* 1 */
    uint8_t hash_type;          /* the CodeDirectory's hash type (cs_blobs.h) */
    uint8_t zero[2];
    uint8_t cdhash[20];
} m27_slice_record;
#define M27_SLICE_MAGIC "m27slice"
/* Is sl the slice the record names? The slice's signature is verified
 * (m27_slice_verify: structure, page hashes) and its CodeDirectory must be
 * the recorded one. 0: yes; -1: no, or the record is not one m27 reads
 * (why). The loader's gate asks this for the main executable of every
 * embedded program (a stub without the section is refused), and of the CLI
 * if its own main executable has the section. */
int m27_libval_bound(const void *record, size_t size, m27_slice *sl, char *why, size_t n);
/* m27_libval_bound with this process's own main executable's record: an
 * embedded program's stub. A stub without one names no code: -1 (why). */
int m27_libval_stub_bound(m27_slice *sl, char *why, size_t n);
enum { M27_LIBVAL_UNDECIDED, M27_LIBVAL_OFF, M27_LIBVAL_KERNEL };
/* How the process started, said by the front end that started it (main.c):
 * M27_ENTRY_CLI, m27's own launcher (`m27 PROGRAM`, every routed exec),
 * which has checked the file it was given (m27_guest_sig_enforce); or
 * M27_ENTRY_EMBEDDED, an embedded program's stub, whose __m27_slice record
 * must name the main executable's slice. */
enum { M27_ENTRY_CLI = 1, M27_ENTRY_EMBEDDED = 2 };
/* The next image loaded is the main executable, started through ENTRY
 * (anything but M27_ENTRY_CLI is taken as M27_ENTRY_EMBEDDED): its
 * signature sets the policy. enabled = false (M27_NO_LIBVAL=1 in an
 * ordinary process; no boot argument turns it off) turns library
 * validation off for a program that does not require it, and is ignored,
 * with a diagnostic, for one that does. A main executable that requires
 * library validation is refused when the process cannot be made to
 * require it (CS_REQUIRE_LV). */
void m27_libval_expect_main(bool enabled, int entry);
/* The loader's unit tests: 1 makes setting CS_REQUIRE_LV fail, 2 reading
 * the status back fail, 3 the flag not stick. */
extern int m27_libval_test_setlv;
/* The loader's gate, after an image's load commands validate and before it
 * is mapped: sl is the slice the image is then built from. img->fd and the
 * slice fields are set. 0 = may load, else -1 with why filled. */
int m27_libval_image(m27_image *img, m27_slice *sl, char *why, size_t n);
int m27_libval_mode(void);
const char *m27_libval_reason(void);

/* Look up an exported symbol in a guest image's export trie. */
bool m27_image_export(m27_image *img, const char *name, uint64_t *addr);
/* The full trie entry: M27_EXPORT_ADDR sets *addr (absolute symbols as
 * stored, others relative to the image); M27_EXPORT_REEXPORT sets *ordinal
 * (the dependency it comes from) and *import_name (NULL: the same name). */
enum { M27_EXPORT_NONE, M27_EXPORT_ADDR, M27_EXPORT_REEXPORT };
int m27_image_export_info(m27_image *img, const char *name, uint64_t *addr, int *ordinal, const char **import_name);

/* Find a section by name; returns its address and size (0 if absent). */
uint64_t m27_image_section(m27_image *img, const char *seg, const char *sect, uint64_t *size);

/* Is `a` exactly the entry point of a guest function? */
bool m27_is_guest_function(uint64_t a);

/* Find the guest image containing a code address (NULL if none). Lock-free:
 * any thread, also in a signal handler, while another thread loads. */
m27_image *m27_image_for_code(uint64_t addr);
/* The same without updating the lookup hint: writes nothing (m27_jit_locate) */
m27_image *m27_image_for_code_peek(uint64_t addr);
m27_image *m27_image_for_addr(uint64_t addr);
/* false: addr lies in no range a guest image was ever registered with, so both lookups above
 * return NULL for it (one search of the immutable range snapshot; any thread, signal handlers) */
bool m27_guest_range_maybe(uint64_t addr);
/* The end of the mapped range of `im` that holds `a` (a segment of a
 * shared-cache image, else the whole image), or 0 if `a` is outside it. */
uint64_t m27_image_extent(const m27_image *im, uint64_t a);
/* The image list, newest first. Only the loader (serialised by runtime.c's
 * load lock) changes it; walk it from any thread with these. */
extern m27_image *m27_images;
static inline m27_image *m27_images_first(void) { return __atomic_load_n(&m27_images, __ATOMIC_ACQUIRE); }
static inline m27_image *m27_images_next(const m27_image *im) { return __atomic_load_n(&im->next, __ATOMIC_ACQUIRE); }

#endif
