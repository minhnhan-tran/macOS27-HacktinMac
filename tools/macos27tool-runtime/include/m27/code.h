/*
 * m27 — guest code bytes.
 *
 * m27 reads guest instructions, and any data the compiler placed among them,
 * only through these functions. Today they return the bytes at the guest
 * address. Each image's executable segments can also be read through an
 * alias (a second, private read-only mapping of the same file; for the
 * arm64e shared cache, of the cache's mappings): m27_code_ptr then returns
 * the alias address, guest address + the image's code_delta. That leaves
 * the guest address range free to hold something else later (A2: x86 entry
 * stubs at guest function starts) while m27 still reads the arm64 bytes.
 *
 * Readers: the JIT's fetch (translate, m27_jit_locate in signal handlers and
 * the AOT writer), the interpreter's fetch (a64_step), swiftcc.c's slot
 * scans, fnslots.c's address-taken scan, blocks.c's look at a register
 * value that lies in a guest image, guest_fault's report, and dsc.c's code
 * analysis (which keeps its own unslid form, dsc.c code_at). Writers of
 * guest instructions (dsc.c's selector and GOT-load rewrites) go through
 * m27_code_write, which changes the guest mapping and the alias alike.
 * Guest code's own loads of its code (jump tables in __text) are guest
 * memory accesses, not m27 reads, and stay as they are.
 *
 * M27_CODE_ALIAS=1 (src/code.c) gives every image an alias and makes every
 * full page of its instruction sections at the guest address PROT_NONE, so
 * that any read that bypasses these functions faults and is reported. A
 * guest load of such a page (a jump table) makes it readable again and is
 * counted. Async-signal-safe: no locks, no allocation.
 */
#ifndef M27_CODE_H
#define M27_CODE_H

#include "m27/macho.h"
#include <stdint.h>
#include <string.h>
#include <sys/types.h>

/* Nonzero once any image's code is read from an alias (src/code.c; the oracle's stubs keep it 0). */
extern int m27_code_aliased;
/* m27_code_ptr when some image is aliased: the image's code_delta, identity outside every image. */
const void *m27_code_ptr_slow(uint64_t a);

/* Where m27 reads the guest code byte at guest address a. */
static inline const void *m27_code_ptr(uint64_t a)
{
    if (__builtin_expect(!__atomic_load_n(&m27_code_aliased, __ATOMIC_RELAXED), 1)) return (const void *)(uintptr_t)a;
    return m27_code_ptr_slow(a);
}

/* The same when the caller already looked up the image whose code ranges hold a (NULL: none). */
static inline const void *m27_code_ptr_im(const m27_image *im, uint64_t a)
{
    return (const void *)(uintptr_t)(im ? a + (uint64_t)im->code_delta : a);
}

/* n bytes at a that may or may not be code (a value read as a pointer): the alias when all n lie in
 * one image's code ranges, else the guest address. */
const void *m27_code_view_slow(uint64_t a, size_t n);
static inline const void *m27_code_view(uint64_t a, size_t n)
{
    if (__builtin_expect(!__atomic_load_n(&m27_code_aliased, __ATOMIC_RELAXED), 1)) return (const void *)(uintptr_t)a;
    return m27_code_view_slow(a, n);
}

static inline uint32_t m27_code_read32(uint64_t a)
{
    uint32_t v;
    memcpy(&v, m27_code_ptr(a), 4);
    return v;
}

/* Change n bytes of guest instructions at guest address a, in the guest
 * mapping and in the alias delta bytes away (0: none). Each page is
 * writable only for the copy and gets its protection back. False (nothing
 * or part written) when a page could not be made writable. */
bool m27_code_write(uint64_t a, int64_t delta, const void *src, size_t n);

/* Writes m27 makes into guest memory the guest may not write (its code, sealed constant data, Swift
 * metadata fields), one at a time: m27_guest_write_begin takes the lock, makes [lo, hi) writable if it
 * was not and returns the protection the range had (-1: none made, not locked); m27_guest_write_end
 * gives it `prot` back (or leaves it, if it was writable) and unlocks; failure stops the process.
 * m27_guest_cas_ro: one word so. m27_code_fork_child: a fork child restores an open write's range. */
int m27_guest_write_begin(uint64_t lo, uint64_t hi);
void m27_guest_write_end(int prot);
bool m27_guest_cas_ro(uint64_t a, uint64_t expected, uint64_t desired);
void m27_code_fork_child(void);

/* ---- aliases and poisoning (src/code.c), for the loaders */
/* M27_CODE_ALIAS (m27_opts_init sets m27_code_alias_mode before any image loads): give images an alias. */
extern bool m27_code_alias_mode;
bool m27_code_alias_wanted(void);
/* On-disk images: reserve img->vmsize for the alias (before the segments are mapped). */
bool m27_code_alias_reserve(m27_image *img);
/* Map one executable segment (runtime address addr, filesize bytes of fd at off, vmsize in all) into
 * the alias, zero-filled past filesize as the loader does at the guest address. bytes (NULL: map the
 * file): the segment's filesize bytes in memory, copied instead, where the loader built the image from
 * a copy of the file (m27_slice). */
bool m27_code_alias_segment(m27_image *img, uint64_t addr, uint64_t filesize, uint64_t vmsize, int fd, off_t off,
                            const void *bytes);
/* Publish the alias: before the image is listed. delta: the image's code_delta. */
void m27_code_alias_publish(m27_image *img, int64_t delta);
/* M27_CODE_ALIAS: make every full page of img's instruction sections at the guest address PROT_NONE. */
void m27_code_poison(m27_image *img);
/* The image is released: its alias goes as its range does, its poisoned ranges are dropped. */
void m27_code_release(m27_image *img, bool keep_range);
/* The fault handler (runtime.c): a fault at addr, a write or not, from the guest's own access
 * (translated code, the interpreter) or not. 0: not a poisoned page; 1: resume (a guest access
 * opened the page, or another thread had); -1: a reader bypassed m27_code_* (*img: its image). */
extern unsigned m27_code_npoison;
int m27_code_poison_fault(uint64_t addr, bool write, bool guest, const m27_image **img);
/* --stats: the m27-code: line (M27_CODE_ALIAS only). */
void m27_code_stats(void);

#endif
