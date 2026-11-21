#ifndef M27_NATIVE_BUNDLE_H
#define M27_NATIVE_BUNDLE_H
#include <stddef.h>

/* Linked identity shim to runtime. */
typedef void *(*m27_bundle_open_fn)(const char *, int, char *, size_t);
typedef int (*m27_bundle_sym_fn)(void *, const char *, void **);
typedef int (*m27_bundle_close_fn)(void *);
typedef const char *(*m27_bundle_class_fn)(const void *);
typedef void *(*m27_real_dlopen_fn)(const char *, int);
typedef m27_real_dlopen_fn (*m27_bundle_install_fn)(m27_bundle_open_fn, m27_bundle_sym_fn, m27_bundle_close_fn, m27_bundle_class_fn);
/* Version 2 (m27_identity_bundle_hooks2): the interposers also pass their caller's return address.
 * expand: an @rpath/@loader_path/@executable_path name dlopen'd by guest code, as a path from the calling
 * guest image (1, out filled) or 0 (not a guest caller, or no file); open2: a non-NULL caller selects
 * only guest-first app libraries; NULL requests ARM-only fallback after native failure.
 * sym2: as sym, and a guest caller may get a guest function's address. A runtime without these keeps version 1. */
typedef int (*m27_bundle_expand_fn)(const char *, const void *, char *, size_t);
typedef void *(*m27_bundle_open2_fn)(const char *, int, const void *, char *, size_t);   /* open, with the caller */
typedef int (*m27_bundle_sym2_fn)(void *, const char *, const void *, void **);
typedef m27_real_dlopen_fn (*m27_bundle_install2_fn)(m27_bundle_open2_fn, m27_bundle_sym2_fn, m27_bundle_close_fn,
                                                     m27_bundle_class_fn, m27_bundle_expand_fn);
void m27_native_bundles_init(void);
#endif
