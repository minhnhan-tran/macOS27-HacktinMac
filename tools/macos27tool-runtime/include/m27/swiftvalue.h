#ifndef M27_SWIFTVALUE_H
#define M27_SWIFTVALUE_H
#include <stdbool.h>
#include <stdint.h>
#include "m27/swift_reverse.h"
struct m27_thread;
struct m27_swift_value_plan;
typedef struct m27_swift_value_scope {
    struct m27_swift_value_scope *previous;
    const struct m27_swift_value_plan *plan;
    void *output;
} m27_swift_value_scope;
void m27_swift_value_begin(struct m27_thread *, m27_swift_value_scope *, const void *metadata, void *output);
void m27_swift_value_end(struct m27_thread *, m27_swift_value_scope *);
bool m27_swift_value_native(struct m27_thread *, uint64_t pc);
/* Guest calls of libswiftCore's Optional value witnesses (swiftvalue.c):
 * set up once the Swift runtime is loaded (per guest image, idempotent); a
 * call target inside [lo, hi] may be one. */
void m27_swift_witness_setup(void);
bool m27_swift_witness_native(struct m27_thread *, uint64_t pc);
extern uint64_t m27_swift_witness_lo, m27_swift_witness_hi;
/* The first guest call of the native function at PC (no exported name): if
 * it is a frozen native struct's value witness called through that struct's
 * metadata, its calls convert like those above. */
void m27_swift_witness_probe(struct m27_thread *, uint64_t pc);
/* A callback's guest code returns its indirect result INDIRECT (0: none) to
 * native code: the nils m27 wrote in the guest's encoding into a value made
 * there since MARK (t->swift_made_n at the callback's entry) get the native
 * encoding back; the ring is then reset to MARK. */
void m27_swift_witness_result(struct m27_thread *, unsigned mark, uint64_t indirect);
/* --stats: intercepted witnesses, those a full table dropped, nils not tracked; the Swift keys the walk's
 * facts table answered for (swiftvalue/Facts.h walk_keys_counts) */
void m27_swift_witness_stats(void);
/* nils m27_swift_witness_result could not restore: their ring entries were overwritten */
uint64_t m27_swift_witness_lost(void);
/* VALUE of TYPE, which native code made in guest storage (a native generic
 * function's result): its outer nils in the guest's encoding where guest code
 * reads them inline. True if a word changed. */
bool m27_swift_native_result_to_guest(const void *type, void *value);
/* VALUE of TYPE, which guest code made and lends to a native function for one
 * call: NULL to lend it as it is, else a copy with its nils in the native
 * encoding to lend instead (free it after the call). */
void *m27_swift_borrow_to_native(const void *type, const void *value);
/* After a guest call of swift_cvw_initStructMetadataWithLayoutString laid
 * out the guest struct METADATA from its N FIELDS (TAGS): its extra
 * inhabitant count set to arm64's, which the guest's compiled witnesses
 * for it select a field by. */
void m27_swift_struct_layout_done(void *metadata, uint64_t n, const void *const *fields, const uint8_t *tags);
/* swift_task_create / swift_asyncLet_begin with result type FUTURE: refused
 * when its values need certification (swiftvalue.c). */
void m27_swift_task_result(const void *future, uint64_t lr);
/* swift_allocObject of METADATA (array storage), or storage of ELEMENT
 * (swift_allocBox with BOX, _allocateUninitializedArray): refused when native
 * code would destroy a nil of the element type in the guest's encoding that
 * the storage's final destruction cannot convert first. */
void m27_swift_storage_alloc(const void *metadata, uint64_t lr);
void m27_swift_storage_element(const void *element, bool box, uint64_t lr);
/* OBJECT, array storage or a generic box guest code just made: its element
 * type's storage gets m27's final-destruction conversion (swiftvalue.c). */
void m27_swift_storage_made(void *object);
void m27_swift_value_fork_child(void);
void m27_swift_result_prepare(const m27_swift_result_context *, const uint64_t *, uint64_t, m27_swift_value_scope *);
#endif
