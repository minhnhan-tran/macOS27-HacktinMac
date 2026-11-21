/* Compiler-lowered Swift reverse signatures emitted by the Swift signature generator
 * (not part of this repository).
 * This is deliberately separate from abi.c's C/ObjC signatures. */
#ifndef M27_SWIFT_REVERSE_H
#define M27_SWIFT_REVERSE_H
#include <stdint.h>

enum { M27_SR_GP, M27_SR_FP, M27_SR_STACK, M27_SR_SPECIAL };
typedef struct {
    uint16_t arm, native;
    uint8_t arm_kind, native_kind, size, reserved;
} m27_swift_move;
typedef struct {
    uint16_t version, nargs, nrets, stack_bytes;
    m27_swift_move moves[];
} m27_swift_reverse;
#define M27_SWIFT_REVERSE_VERSION 1
/* hsig allocations are aligned; low bits 10 distinguish Swift from C. */
#define M27_CB_SWIFT_TAG 2ull
/* Optional compiler semantic companion. This identifies a result type, not
 * permission to reinterpret its representation or transfer its ownership. */
typedef struct {
    uint16_t version, self_arg, witness_arg, result_arg;
    const char *associated_name;
} m27_swift_result_signature;
/* Version 1 remains the 16-byte identity-only prefix. Version 2 adds a
 * compiler-established contract; it does not prove representation equality. */
enum { M27_SWIFT_RESULT_COPYABLE = 1u };
typedef struct {
    m27_swift_result_signature base;
    uint32_t flags, reserved;
} m27_swift_result_signature_v2;
static inline uint32_t m27_swift_result_contract(const m27_swift_result_signature *s)
{
    return s && s->version == 2 ? ((const m27_swift_result_signature_v2 *)s)->flags : 0;
}
typedef struct m27_swift_result_context m27_swift_result_context;
#define M27_CB_SWIFT_RESULT_TAG 3ull

const m27_swift_reverse *m27_thunks_reverse(const char *key);
const m27_swift_reverse *m27_swift_reverse_requirement(uint64_t requirement,
    const m27_swift_result_signature **result);
const m27_swift_result_signature *m27_thunks_result(const char *key, const m27_swift_reverse *reverse);
const m27_swift_result_context *m27_swift_result_bind(uint64_t protocol, uint64_t requirement,
    const m27_swift_reverse *reverse, const m27_swift_result_signature *signature);
const m27_swift_reverse *m27_swift_result_reverse(const m27_swift_result_context *context);
void m27_swift_result_check(const m27_swift_result_context *context, const uint64_t *arguments, uint64_t target);
void m27_swift_result_stats(void);
void m27_swift_result_fork_child(void);
#endif
