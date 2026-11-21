/*
 * m27 — which code stands for which Apple program (docs/security-model.md, Identity).
 *
 * Two root-owned lists in /usr/lib/m27, one format:
 *   stub-identities    tools/m27-trust-list --stubs (m27-image-payload): each embedded Apple
 *                      program's x86_64 stub -> the program's own Apple arm64 slice;
 *   native-identities  the image builder: a native program it re-signed ad hoc -> the
 *                      Apple-signed original it was made from.
 * One row per line ('#' comments):
 *   <observed cdhash, 40 hex>  <reference cdhash, 40 hex>  <arch>  <reference path>
 * observed: the cdhash the kernel reports for the running code. reference: the cdhash of the
 * Apple-signed slice `arch` (arm64e, arm64, x86_64, ...) of the file at `path` (relative to the
 * System root, may contain spaces), whose signature answers for it.
 *
 * Self-contained (no libm27): libm27 reads it for the switch tier (trust.c), and libm27_codeid
 * (src/code_identity.c) for Security.framework. A list is read only if root owns it and every
 * directory above it, none of them is writable by group or others, none is a symbolic link and
 * none has an extended ACL; it is read once per process. A row whose reference cdhash is
 * itself an observed cdhash, or equals its own observed cdhash, is dropped (no chains).
 */
#ifndef M27_IDLIST_H
#define M27_IDLIST_H
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint8_t observed[20], reference[20];
    char arch[16];
    char path[1024];              /* absolute */
} m27_idrow;

/* The row for a running or static code's cdhash, or NULL. */
const m27_idrow *m27_idlist_find(const uint8_t cdhash[20]);

#ifdef M27_TEST_SWITCHES
/* Tests: read the lists from dir instead of /usr/lib/m27, where the owner may be the
 * current user. Call before the first m27_idlist_find. */
void m27_idlist_use_dir_for_test(const char *dir);
#endif
#endif
