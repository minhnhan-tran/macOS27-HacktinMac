/* Minimal runtime symbols so jit.o links into the oracle checker. */
#include "m27/runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
m27_image *m27_image_for_code(uint64_t a) { (void)a; return 0; }
m27_image *m27_image_for_code_peek(uint64_t a) { (void)a; return 0; }
int m27_code_aliased;
const void *m27_code_ptr_slow(uint64_t a) { return (const void *)(uintptr_t)a; }
const void *m27_code_view_slow(uint64_t a, size_t n) { (void)n; return (const void *)(uintptr_t)a; }
bool m27_is_trap(uint64_t pc) { (void)pc; return false; }
int m27_verbose;
void m27_callback_entry(void) {}
int m27_use_jit = 1;
int m27_env_tier(void) { return 0; }
void m27_opts_init(const char *flags) { (void)flags; }
FILE *m27_diag_file;
void m27_diag_prefix(FILE *f) { fprintf(f, "m27[oracle]: "); }
void m27_oom(const char *what) { fprintf(stderr, "m27[oracle]: %s: out of memory\n", what); abort(); }
void m27_crit_enter(void) {}
void m27_fatal_safe(const char *what, const uint64_t *v, int n)
{
    (void)v; (void)n;
    write(2, what, strlen(what));
    write(2, "\n", 1);
    abort();
}
void m27_crit_leave(void) {}
int m27_crit_depth(void) { return 1; }
/* jit.o's AOT writer and loader (never reached from the oracle) */
m27_image *m27_map_image_only(const char *path, char *err, size_t errlen) { (void)path; (void)err; (void)errlen; return 0; }
void m27_unload_image(m27_image *img) { (void)img; }
m27_slice *m27_slice_open(int fd, char *why, size_t n) { (void)fd; (void)why; (void)n; return 0; }
void m27_slice_close(m27_slice *sl) { (void)sl; }
int m27_slice_verify(m27_slice *sl, bool *bad_exec, char *why, size_t n) { (void)sl; (void)bad_exec; (void)why; (void)n; return -1; }
bool m27_codesig_library_validated(const m27_codesig *cs) { (void)cs; return false; }
bool m27_guest_sig_cdhash(const uint8_t *blob, size_t len, uint8_t cdhash[20], uint8_t *hash_type, char *why, size_t n)
{
    (void)blob; (void)len; (void)cdhash; (void)hash_type; (void)why; (void)n;
    return false;
}
