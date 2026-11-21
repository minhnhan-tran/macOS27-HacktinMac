/*
 * m27 — native callback-call contracts: the ABI a native image uses at one of its own indirect calls that can
 * reach guest code (src/native_callback.c).
 *
 * A record names a native image by its LC_UUID, the call by the offset of its return address from the
 * image's Mach-O header, and the lowered Swift signature of the callee (a m27_swift_reverse recipe: which
 * native register or stack slot each guest register is taken from). It describes the outgoing call, not the
 * function that contains it, and so holds for any guest function that call reaches: a closure, a
 * reabstraction thunk, a partial-apply forwarder, an entry reached by a fault on the guest page. The
 * callback path consults it before inferring a convention from the guest callee's code.
 *
 * Records are bound when their image is loaded: the UUID must match, the return address must lie in the
 * image's executable __TEXT and follow an indirect near call (FF /2), and the recipe must be well formed. A
 * record that fails, or two that disagree, bind nothing. Lookups take no lock and allocate nothing.
 */
#ifndef M27_NATIVE_CALLBACK_H
#define M27_NATIVE_CALLBACK_H
#include <stdbool.h>
#include <stdint.h>
#include "m27/gmap.h"
#include "m27/swift_reverse.h"

#define M27_NATIVE_CALLBACK_VERSION 1
typedef struct {
    uint32_t version;                  /* M27_NATIVE_CALLBACK_VERSION */
    uint8_t uuid[16];                  /* the native image's LC_UUID */
    uint64_t ret_offset;               /* the call's return address - the image's Mach-O header */
    const m27_swift_reverse *recipe;   /* the callee's lowered signature */
} m27_native_callback_record;

/* Why RECIPE is not a usable callback recipe, or NULL. */
const char *m27_native_callback_recipe_check(const m27_swift_reverse *recipe);
/* Binds into MAP (M27_GMAP_INIT(2, 1, ...): return pc -> recipe) the records of ROWS whose UUID is the
 * image's at HEADER; returns how many, and counts in *REJECTED those of this image that failed. The caller
 * holds MAP's writer lock. */
unsigned m27_native_callback_bind(m27_gmap *map, const void *header, const m27_native_callback_record *rows,
                                  unsigned n, unsigned *rejected);
static inline const m27_swift_reverse *m27_native_callback_lookup(const m27_gmap *map, uint64_t return_pc)
{
    const uint64_t *e = m27_gmap_get1(map, return_pc);
    return e ? (const m27_swift_reverse *)(uintptr_t)e[1] : 0;
}

/* The records m27 ships (src/native_callback.c), and the runtime's table of the loaded ones (src/bridge.c). */
extern const m27_native_callback_record m27_native_callback_rows[];
extern const unsigned m27_native_callback_nrows;
const m27_swift_reverse *m27_bridge_native_callback_reverse(uint64_t return_pc);
#endif
