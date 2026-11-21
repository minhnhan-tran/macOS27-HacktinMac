/*
 * m27 — M27_DLSYM("name"): dlsym(RTLD_DEFAULT, "name") looked up once per
 * call site. RTLD_DEFAULT walks every loaded image's export trie (through
 * libm27_identity's dlsym interposer), and the Swift value code names the
 * same libswiftCore, Foundation and CoreGraphics symbols on every value it
 * converts: per call, that walk was most of Dock's and ControlCenter's CPU
 * time in their layout passes. A found address is kept (the libraries that
 * define these symbols are never unloaded); a symbol not found is looked up
 * again on the next call, since its library may load later. The name must
 * be a string literal: the cache belongs to the call site, not the name.
 * No thread-local storage: one static word per site, written atomically
 * (two threads racing store the same address).
 */
#ifndef M27_DLSYM_ONCE_H
#define M27_DLSYM_ONCE_H

#include <dlfcn.h>

#define M27_DLSYM(name) (__extension__({                                          \
    static void *m27_dlsym_site_;                                                 \
    void *m27_dlsym_v_ = __atomic_load_n(&m27_dlsym_site_, __ATOMIC_RELAXED);     \
    if (__builtin_expect(!m27_dlsym_v_, 0)) {                                     \
        m27_dlsym_v_ = dlsym(RTLD_DEFAULT, "" name);                              \
        if (m27_dlsym_v_) __atomic_store_n(&m27_dlsym_site_, m27_dlsym_v_, __ATOMIC_RELAXED); \
    }                                                                             \
    m27_dlsym_v_; }))

#endif
