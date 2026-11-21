/*
 * m27 — generated thunk pairs (m27/bridge.h).
 *
 * The thunk generator (not part of this repository) writes one source per
 * native function whose signature the register bridge cannot carry,
 * compiles it twice and links the halves into one fat thunk library:
 * the arm64e slice holds the guest halves (_m27_gh_KEY: the function's
 * own prototype lowered by clang for AAPCS64), the x86_64 slice the native
 * halves (_m27_nh_KEY: the same prototype lowered for SysV). Interface-unit records name their pair by
 * KEY (M27_IFR_THUNK); the key is a hash of the pair's source, so a record
 * and a library from different builds never meet on the wrong code: a key
 * the libraries do not have leaves the plan without a pair (its abi.c encoding
 * when supplied, otherwise REFUSED), counted in m27_bridge_stats.
 *
 * Thunk libraries are the files NAME.thunks of the interface-unit
 * directory, opened under the same trust rules (m27_aot_open_trusted). They
 * load once per process, before the main image binds: after that a
 * platform or hardened guest has library validation on (src/libval.c), and
 * the kernel would refuse an ad hoc native library. The x86_64 slice loads
 * with dlopen, the arm64e slice as a guest image (runtime.c).
 */
#include "m27/runtime.h"
#include "m27/bridge.h"
#include <dirent.h>
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Written once by m27_thunks_init (startup, one thread), read afterwards. */
typedef struct { void *handle; m27_image *img; } tlib;
static tlib *tlibs;
static int ntlibs;
static uint64_t st_found, st_missing;

void m27_thunks_init(void)
{
    if (m27_opt.no_ifunits || m27_opt.no_thunks || !m27_have_thunks()) return;
    m27_aot_dir dir;
    char why[512];
    if (!m27_if_dirs_for(m27_env_tier(), m27_opt.if_dir, &dir, why, sizeof why)) return;
    DIR *d = opendir(dir.path);
    if (!d) return;
    struct dirent *e;
    char **names = NULL;
    size_t n = 0, cap = 0;
    while ((e = readdir(d))) {
        size_t l = strlen(e->d_name);
        if (l <= 7 || e->d_name[0] == '.' || strcmp(e->d_name + l - 7, ".thunks")) continue;
        if (n == cap) { cap = cap ? cap * 2 : 16; names = m27_alloc_check(realloc(names, cap * sizeof *names), "thunk library list"); }
        names[n++] = m27_alloc_check(strdup(e->d_name), "thunk library list");
    }
    closedir(d);
    if (n) tlibs = m27_alloc_check(calloc(n, sizeof *tlibs), "thunk library list");
    for (size_t k = 0; k < n; k++) {
        struct stat st;
        int fd = m27_aot_open_trusted(&dir, names[k], &st, why, sizeof why);
        if (fd < 0) {
            if (m27_verbose || m27_bridge_counting) { M27_LOG(0, "thunk library %s/%s rejected: %s\n", dir.path, names[k], why); }
            continue;
        }
        close(fd);
        char path[2048];
        if ((size_t)snprintf(path, sizeof path, "%s/%s", dir.path, names[k]) >= sizeof path) {
            M27_LOG(0, "thunk library %s/%s: path too long, not loaded\n", dir.path, names[k]);
            continue;
        }
        void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
        if (!h) {
            M27_LOG(0, "thunk library %s: native half not loaded: %s\n", path, dlerror());
            continue;
        }
        char err[512];
        m27_image *im = m27_load_guest_library(path, err, sizeof err);
        if (!im) {
            M27_LOG(0, "thunk library %s: guest half not loaded: %s\n", path, err);
            continue;
        }
        tlibs[ntlibs].handle = h;
        tlibs[ntlibs].img = im;
        ntlibs++;
        M27_LOG(1, "thunk library %s (guest half at %#llx)\n", path, (unsigned long long)im->base);
    }
    for (size_t k = 0; k < n; k++) free(names[k]);
    free(names);
}

bool m27_thunks_pair(const char *key, uint64_t *gh, uint64_t *nh)
{
    char gname[96], nname[96];
    snprintf(gname, sizeof gname, "_m27_gh_%s", key);
    snprintf(nname, sizeof nname, "m27_nh_%s", key);
    for (int k = 0; k < ntlibs; k++) {
        uint64_t g = 0;
        int ord = 0;
        const char *imp = NULL;
        if (m27_image_export_info(tlibs[k].img, gname, &g, &ord, &imp) != M27_EXPORT_ADDR || !g) continue;
        void *n = dlsym(tlibs[k].handle, nname);
        if (!n) continue;
        *gh = g;
        *nh = (uint64_t)(uintptr_t)n;
        m27_bridge_note_native_half(*nh);
        __atomic_fetch_add(&st_found, 1, __ATOMIC_RELAXED);
        return true;
    }
    __atomic_fetch_add(&st_missing, 1, __ATOMIC_RELAXED);
    return false;
}

const m27_swift_reverse *m27_thunks_reverse(const char *key)
{
    char name[96];
    snprintf(name, sizeof name, "m27_rs_%s", key);
    for (int k = 0; k < ntlibs; k++) {
        const m27_swift_reverse *s = dlsym(tlibs[k].handle, name);
        if (!s) continue;
        if (((uintptr_t)s & 7) || s->version != M27_SWIFT_REVERSE_VERSION || s->nargs > 128 ||
            s->nrets > 8 || s->stack_bytes > 4096 || (s->stack_bytes & 15)) return NULL;
        for (unsigned j = 0; j < s->nargs + s->nrets; j++) {
            const m27_swift_move *m = &s->moves[j];
            bool ret = j >= s->nargs;
            if (m->reserved || (m->size != 1 && m->size != 2 && m->size != 4 && m->size != 8 && m->size != 16)) return NULL;
            if (m->arm_kind == M27_SR_GP) { if (m->arm >= (ret ? 4 : 31) || m->size > 8) return NULL; }
            else if (m->arm_kind == M27_SR_FP) { if (m->arm >= (ret ? 4 : 8)) return NULL; }
            else if (m->arm_kind == M27_SR_STACK && !ret) { if (m->arm + m->size > s->stack_bytes) return NULL; }
            else return NULL;
            if (m->native_kind == M27_SR_GP) { if (m->native >= (ret ? 4 : 6) || m->size > 8) return NULL; }
            else if (m->native_kind == M27_SR_FP) { if (m->native >= (ret ? 4 : 8)) return NULL; }
            else if (m->native_kind == M27_SR_STACK && !ret) { if (m->native + m->size > 4096) return NULL; }
            else if (m->native_kind == M27_SR_SPECIAL && !ret) { if (m->native >= 4 || m->size != 8) return NULL; }
            else return NULL;
        }
        return s;
    }
    return NULL;
}

/* An exported requirement descriptor (Tq) names the corresponding dispatch
 * thunk (Tj). Swift IRGen GenThunk.cpp uses the requirement's function type,
 * including Self metadata and witness table, for that thunk. Its UUID-bound
 * interface unit supplies the compiler-verified signature. The descriptor and
 * dispatch are resolved by exact exports from that unit, independently of
 * dladdr's incomplete local symbol names. Unsupported requirements stay untyped. */
const m27_swift_reverse *m27_swift_reverse_requirement(uint64_t req,
                                                     const m27_swift_result_signature **result)
{
    if (result) *result = NULL;
    if (m27_opt.no_thunks || m27_opt.no_ifunits || !ntlibs || m27_image_for_addr(req)) return NULL;
    return m27_bridge_swift_requirement(req, result);
}

const m27_swift_result_signature *m27_thunks_result(const char *key, const m27_swift_reverse *reverse)
{
    char rname[96], name[96];
    snprintf(rname, sizeof rname, "m27_rs_%s", key);
    snprintf(name, sizeof name, "m27_ar_%s", key);
    for (int k = 0; k < ntlibs; k++) {
        /* The companion and register recipe must come from the same unit. */
        if (dlsym(tlibs[k].handle, rname) != reverse) continue;
        const m27_swift_result_signature *s = dlsym(tlibs[k].handle, name);
        if (!s || ((uintptr_t)s & 7) || (s->version != 1 && s->version != 2) || !s->associated_name ||
            reverse->nrets || s->self_arg >= reverse->nargs ||
            s->witness_arg >= reverse->nargs || s->result_arg >= reverse->nargs ||
            s->self_arg == s->witness_arg || s->self_arg == s->result_arg ||
            s->witness_arg == s->result_arg) return NULL;
        if (reverse->moves[s->self_arg].size != 8 || reverse->moves[s->witness_arg].size != 8 ||
            reverse->moves[s->result_arg].size != 8) return NULL;
        if (s->version == 2) {
            const m27_swift_result_signature_v2 *v2 = (const void *)s;
            if (v2->flags != M27_SWIFT_RESULT_COPYABLE || v2->reserved) return NULL;
        }
        return s;
    }
    return NULL;
}

void m27_thunks_stats(void)
{
    if (!ntlibs && !st_missing) return;
    fprintf(M27_DIAG, "m27: thunk pairs: %d libraries, %llu pairs bound, %llu keys not found\n", ntlibs,
            (unsigned long long)st_found, (unsigned long long)st_missing);
}
