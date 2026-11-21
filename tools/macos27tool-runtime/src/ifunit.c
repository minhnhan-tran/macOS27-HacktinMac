/*
 * m27 — interface units (m27/bridge.h): the validated parser and the
 * directory list. Pure: no runtime state, so it is in libm27core.a and the
 * fuzz target and tests/aot/policy.c link it alone.
 */
#include "m27/bridge.h"
#include <stdio.h>
#include <string.h>

#define REJ(...) do { if (why && n) snprintf(why, n, __VA_ARGS__); return -1; } while (0)

static bool narrow_kind(uint8_t k)
{
    return k == 'B' || k == 'c' || k == 'C' || k == 's' || k == 'S' ||          /* C narrow results */
           k == 'b' || k == 'h' || k == 'H' || k == 't' || k == 'T';            /* Swift async narrow results */
}

int m27_ifunit_stackpack(const char *enc, uint8_t off[M27_IF_MAX_PACK], uint8_t width[M27_IF_MAX_PACK])
{
    int n = 0;
    unsigned end = 0;
    const char *e = enc;
    if (!e || !*e) return -1;
    for (;;) {
        if (n == M27_IF_MAX_PACK || *e < '0' || *e > '9' || (e[0] == '0' && e[1] >= '0' && e[1] <= '9')) return -1;
        unsigned o = 0;
        while (*e >= '0' && *e <= '9') { o = o * 10 + (unsigned)(*e++ - '0'); if (o > 8 * M27_IF_MAX_PACK) return -1; }
        if (*e++ != '.') return -1;
        unsigned w = (unsigned)(*e++ - '0');
        if (w != 1 && w != 2 && w != 4 && w != 8) return -1;
        if (o % w || o < end || o + w > 8 * M27_IF_MAX_PACK) return -1;
        off[n] = (uint8_t)o; width[n] = (uint8_t)w; n++;
        end = o + w;
        if (!*e) return n;
        if (*e++ != ',') return -1;
    }
}

int m27_ifunit_check(const void *buf, uint64_t size, const uint8_t *uuid, m27_if_view *v, char *why, size_t n)
{
    const uint8_t *b = buf;
    m27_if_hdr h;
    _Static_assert(sizeof(m27_if_hdr) == 64 && sizeof(m27_if_rec) == 24, "interface unit layout");
    if ((uintptr_t)buf & 3) REJ("unaligned buffer");
    if (size < sizeof h) REJ("truncated (%llu bytes)", (unsigned long long)size);
    memcpy(&h, b, sizeof h);
    if (memcmp(h.magic, M27_IF_MAGIC, 8) != 0) REJ("not a m27 interface unit");
    if (h.version != M27_IF_VERSION) REJ("format version %u, expected %u", h.version, M27_IF_VERSION);
    if (h.hdr_size != sizeof h) REJ("header size %u, expected %zu", h.hdr_size, sizeof h);
    if (h.rec_size != sizeof(m27_if_rec)) REJ("record size %u, expected %zu", h.rec_size, sizeof(m27_if_rec));
    if (h.file_size != size) REJ("file is %llu bytes, header says %llu", (unsigned long long)size, (unsigned long long)h.file_size);
    if (size > M27_IF_MAX_FILE) REJ("file larger than %llu MB", M27_IF_MAX_FILE >> 20);
    if (uuid && memcmp(h.uuid, uuid, 16) != 0) REJ("library UUID differs");
    if (h.nrecs > M27_IF_MAX_RECS) REJ("%u records", h.nrecs);
    uint64_t recs_end = sizeof h + (uint64_t)h.nrecs * sizeof(m27_if_rec);
    if (h.strtab_off != recs_end) REJ("string table at %#x, records end at %#llx", h.strtab_off, (unsigned long long)recs_end);
    if (h.strtab_size < 1 || (uint64_t)h.strtab_off + h.strtab_size != size)
        REJ("string table of %u bytes at %#x in a %llu-byte file", h.strtab_size, h.strtab_off, (unsigned long long)size);
    if (m27_aot_hash64(b + sizeof h, (size_t)(size - sizeof h), 0) != h.payload_hash) REJ("payload hash mismatch");
    const m27_if_rec *recs = (const m27_if_rec *)(const void *)(b + sizeof h);
    const char *str = (const char *)b + h.strtab_off;
    uint32_t ss = h.strtab_size;
    if (str[0] || str[ss - 1]) REJ("string table not NUL-delimited");
    const char *prev = NULL;
    for (uint32_t k = 0; k < h.nrecs; k++) {
        m27_if_rec r;
        memcpy(&r, &recs[k], sizeof r);
        if (!r.name || r.name >= ss || !str[r.name]) REJ("record %u: no name", k);
        const char *name = str + r.name;
        if (prev && strcmp(prev, name) >= 0) REJ("record %u (%s): names not strictly increasing", k, name);
        prev = name;
        if (r.enc >= ss || r.why >= ss || r.thunk >= ss) REJ("record %u (%s): string offset outside the table", k, name);
        if (r.cls >= M27_BR_NCLASS) REJ("record %u (%s): class %u", k, name, r.cls);
        if (r.flags & ~M27_IFR_FLAGS) REJ("record %u (%s): unknown flags %#x", k, name, r.flags);
        if (r.resmask & ~(M27_RES_CODE | M27_RES_BLOCK)) REJ("record %u (%s): unknown result bits %#x", k, name, r.resmask);
        if (r.rsv) REJ("record %u (%s): reserved field %#x", k, name, r.rsv);
        bool sig = r.flags & M27_IFR_SIG, thk = r.flags & M27_IFR_THUNK;
        if (r.kind == M27_IF_KIND_STACKPACK) {
            uint8_t po[M27_IF_MAX_PACK], pw[M27_IF_MAX_PACK];
            if (r.cls != M27_BR_TYPED || sig || thk || (r.flags & M27_IFR_PROVEN) || !r.enc ||
                m27_ifunit_stackpack(str + r.enc, po, pw) < 0)
                REJ("record %u (%s): stack pack not TYPED with a valid layout and no other conversion", k, name);
        } else if (r.kind == 'Z') {
            if ((r.flags & (M27_IFR_SIG | M27_IFR_THUNK)) || !r.enc || !m27_cxxstr_enc_ok(str + r.enc))
                REJ("record %u (%s): a std::string conversion (kind Z) needs its roles in enc", k, name);
        } else if (r.kind == 'D') {
            if (r.cls != M27_BR_TYPED || (r.flags & (M27_IFR_SIG | M27_IFR_THUNK | M27_IFR_PROVEN)) || !r.enc ||
                !m27_optdata_enc_ok(str + r.enc))
                REJ("record %u (%s): a Data? conversion (kind D) is TYPED with its roles in enc", k, name);
        } else if (r.kind && !narrow_kind(r.kind)) REJ("record %u (%s): result kind %#x", k, name, r.kind);
        const char *proof = str + r.why;
        bool code = !strncmp(proof, "source=code", 11), proven = r.flags & M27_IFR_PROVEN;
        if (code || proven) {
            bool scalar = !strncmp(proof, M27_IF_PROOF_PREFIX, sizeof(M27_IF_PROOF_PREFIX) - 1) &&
                          (r.flags & M27_IFR_MASKED) && (r.cls == M27_BR_EXACT || r.cls == M27_BR_TYPED);
            bool result = !strncmp(proof, M27_IF_RESULT_PROOF_PREFIX, sizeof(M27_IF_RESULT_PROOF_PREFIX) - 1) &&
                          !(r.flags & M27_IFR_MASKED) && !r.argmask && !str[r.enc] &&
                          r.cls == M27_BR_TYPED && r.kind && strchr("BcCsS", r.kind);
            if (!code || !proven || !(scalar || result) ||
                sig || thk || (r.argmask & ~0x3fu) || r.resmask || (r.kind && !strchr("BcCsS", r.kind)))
                REJ("record %u (%s): stale or invalid code proof; regenerate interface units", k, name);
        }
        if (sig && (!r.enc || !str[r.enc] || r.kind)) REJ("record %u (%s): a signature call needs an encoding and no kind", k, name);
        if (thk != (r.thunk != 0)) REJ("record %u (%s): a thunk key needs the thunk flag and the flag a key", k, name);
        if (thk) {
            const char *key = str + r.thunk;
            size_t kl = strnlen(key, ss - r.thunk);
            if (!kl || kl > M27_IF_MAX_KEY || strspn(key, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_") != kl)
                REJ("record %u (%s): thunk key not 1-%d of [A-Za-z0-9_]", k, name, M27_IF_MAX_KEY);
            if (r.kind) REJ("record %u (%s): a thunk pair with a result kind", k, name);
        }
        for (const char *e = r.enc ? str + r.enc : ""; *e; e++)
            if ((unsigned char)*e < 0x21 || (unsigned char)*e > 0x7e) REJ("record %u (%s): encoding byte %#x", k, name, (unsigned char)*e);
        switch (r.cls) {
        case M27_BR_EXACT:
            if (sig || thk || r.kind) REJ("record %u (%s): EXACT with a conversion", k, name);
            break;
        case M27_BR_TYPED:
            if (!sig && !thk && !r.kind) REJ("record %u (%s): TYPED without a conversion", k, name);
            break;
        case M27_BR_REFUSED:
            if (!r.why || !str[r.why]) REJ("record %u (%s): REFUSED without a reason", k, name);
            if (sig || thk || r.kind || (r.flags & M27_IFR_MASKED)) REJ("record %u (%s): REFUSED with a conversion", k, name);
            break;
        default:   /* UNKNOWN: the full scan, nothing else */
            if (sig || thk || r.kind || (r.flags & M27_IFR_MASKED)) REJ("record %u (%s): UNKNOWN with a type", k, name);
            break;
        }
    }
    if (v) *v = (m27_if_view){(const m27_if_hdr *)buf, recs, str, h.nrecs, ss};
    return 0;
}

const m27_if_rec *m27_ifunit_find(const m27_if_view *v, const char *name)
{
    uint32_t lo = 0, hi = v->nrecs;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        int r = strcmp(name, v->str + v->recs[mid].name);
        if (!r) return &v->recs[mid];
        if (r < 0) hi = mid; else lo = mid + 1;
    }
    return NULL;
}

int m27_if_dirs_for(int tier, const char *if_dir, m27_aot_dir *out, char *why, size_t n)
{
    if (why && n) why[0] = 0;
    if (tier == M27_ENV_OPEN && if_dir && if_dir[0]) {
        if (if_dir[0] != '/') { if (why && n) snprintf(why, n, "M27_IF_DIR=%s is not absolute; no interface units", if_dir); return 0; }
        if (strlen(if_dir) >= sizeof out[0].path) { if (why && n) snprintf(why, n, "M27_IF_DIR is too long; no interface units"); return 0; }
        snprintf(out[0].path, sizeof out[0].path, "%s", if_dir);
        out[0].kind = M27_AOT_EXPLICIT;
        return 1;
    }
    snprintf(out[0].path, sizeof out[0].path, "%s", M27_IF_SYS_DIR);
    out[0].kind = M27_AOT_SYS_RO;
    return 1;
}
