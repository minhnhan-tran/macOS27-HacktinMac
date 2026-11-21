/*
 * m27 — the host's optional x86 features, in m27's own enumeration.
 *
 * Everything m27 requires of the CPU (x86-64-v2 and CMPXCHG16B; src/main.c
 * refuses to start without it) is assumed and not listed. The bits below are
 * the extensions a translation may use only when the host has them. They are
 * computed once per process from CPUID leaf 1, leaf 7 subleaf 0 and leaf
 * 0x80000001, and for the extensions that need operating-system state (the
 * VEX and EVEX register forms) also from OSXSAVE and XCR0: CPUID alone does
 * not make those instructions executable.
 *
 * The word is immutable for the life of a process. Emission never reads it
 * directly: a translation carries the profile it was made under (jit.c tctx),
 * which is the process's for the JIT and the file's for an AOT cache, so a
 * block re-emitted for fault recovery gives the same bytes. An AOT cache
 * records its profile and loads only on a host that has every bit of it
 * (m27/aot.h).
 *
 * src/cpufeat.c is pure apart from the CPUID/XGETBV probe and reads no
 * switch: in libm27core.a, so the oracle, unit and policy tests link it.
 */
#ifndef M27_CPUFEAT_H
#define M27_CPUFEAT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define M27_CPU_AES        (1ull << 0)   /* AES-NI (leaf 1 ECX 25) */
#define M27_CPU_PCLMUL     (1ull << 1)   /* PCLMULQDQ (leaf 1 ECX 1) */
#define M27_CPU_AVX        (1ull << 2)   /* AVX (leaf 1 ECX 28) with OSXSAVE and XCR0 SSE|AVX state */
#define M27_CPU_F16C       (1ull << 3)   /* F16C (leaf 1 ECX 29), VEX: needs M27_CPU_AVX's state */
#define M27_CPU_RDRAND     (1ull << 4)   /* RDRAND (leaf 1 ECX 30) */
#define M27_CPU_FMA        (1ull << 5)   /* FMA3 (leaf 1 ECX 12), VEX */
#define M27_CPU_MOVBE      (1ull << 6)   /* MOVBE (leaf 1 ECX 22) */
#define M27_CPU_AVX2       (1ull << 7)   /* AVX2 (leaf 7 EBX 5), VEX */
#define M27_CPU_BMI1       (1ull << 8)   /* BMI1 (leaf 7 EBX 3): VEX-encoded, general registers only */
#define M27_CPU_BMI2       (1ull << 9)   /* BMI2 (leaf 7 EBX 8): likewise */
#define M27_CPU_LZCNT      (1ull << 10)  /* LZCNT (leaf 0x80000001 ECX 5) */
#define M27_CPU_ADX        (1ull << 11)  /* ADX (leaf 7 EBX 19) */
#define M27_CPU_SHA        (1ull << 12)  /* SHA-NI (leaf 7 EBX 29) */
#define M27_CPU_GFNI       (1ull << 13)  /* GFNI (leaf 7 ECX 8), the SSE-encoded forms */
#define M27_CPU_VAES       (1ull << 14)  /* VAES (leaf 7 ECX 9), VEX/EVEX */
#define M27_CPU_VPCLMUL    (1ull << 15)  /* VPCLMULQDQ (leaf 7 ECX 10), VEX/EVEX */
#define M27_CPU_AVX512F    (1ull << 16)  /* AVX-512 F (leaf 7 EBX 16) with XCR0 opmask|ZMM state */
#define M27_CPU_AVX512DQ   (1ull << 17)  /* leaf 7 EBX 17, with M27_CPU_AVX512F's state */
#define M27_CPU_AVX512BW   (1ull << 18)  /* leaf 7 EBX 30 */
#define M27_CPU_AVX512VL   (1ull << 19)  /* leaf 7 EBX 31 */
#define M27_CPU_ALL        ((1ull << 20) - 1)

/* What every Intel Mac from 2012 on has: the Ivy Bridge set (MacBookPro9,2
 * is the oldest machine a release supports). `m27 --aot --cpu=baseline`
 * generates for this, intersected with the host (it can only clear bits). */
#define M27_CPU_BASELINE   (M27_CPU_AES | M27_CPU_PCLMUL | M27_CPU_AVX | M27_CPU_F16C | M27_CPU_RDRAND)

/* The bits the translator consults (jit.c). A profile is reduced to these
 * before it is used or recorded, so a cache names only what its code can
 * contain, and a build that consults no new bit makes the same cache on
 * every host that has these. */
#define M27_CPU_JIT        (M27_CPU_AES | M27_CPU_PCLMUL)

/* The raw registers the word is computed from (for tests). */
typedef struct {
    uint32_t max_leaf, max_ext;     /* leaf 0 EAX, leaf 0x80000000 EAX */
    uint32_t l1_ecx, l1_edx;        /* leaf 1 */
    uint32_t l7_ebx, l7_ecx;        /* leaf 7 subleaf 0 (zero when max_leaf < 7) */
    uint32_t e1_ecx;                /* leaf 0x80000001 (zero when max_ext < 0x80000001) */
    uint64_t xcr0;                  /* XGETBV(0) when OSXSAVE is set, else 0 */
} m27_cpuid_regs;

/* Pure: the feature word these registers describe. */
uint64_t m27_cpu_decode(const m27_cpuid_regs *r);
/* This host's word, probed once, before any switch. */
uint64_t m27_cpu_host(void);
/* What m27 may use in this process: m27_cpu_host() less the bits
 * M27_CPU_FEATURES clears (tests; a M27_TEST_SWITCHES build only, m27/opts.h).
 * Constant once the switch snapshot is taken (src/opts.c, beside it). */
uint64_t m27_cpu_features(void);

/* "aes,pclmul" (empty for 0); unnamed bits as "bitN". Returns out. */
const char *m27_cpu_names(uint64_t mask, char *out, size_t n);
/* `m27 --aot --cpu=SPEC`: "baseline" (M27_CPU_BASELINE & host), "host", or
 * a hexadecimal mask (with or without 0x) intersected with host. The result
 * never has a bit host lacks. false: why says. */
bool m27_cpu_profile_parse(const char *spec, uint64_t host, uint64_t *out, char *why, size_t n);

#endif
