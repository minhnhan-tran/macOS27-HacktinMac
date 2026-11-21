#ifndef M27_IDENTITY_H
#define M27_IDENTITY_H
#include <stdbool.h>
#include <stdint.h>
/* Addresses refer to mapped guest segments, not a reserved span or code alias.
 * The loader retains image metadata/path storage after release; readers may
 * retain these pointers as they can retain a native dladdr result. */
typedef struct {
    const char *path;
    const void *header;
    intptr_t slide;
} m27_identity_info;
typedef bool (*m27_identity_lookup_fn)(const void *, m27_identity_info *);
void m27_identity_set_image_lookup(m27_identity_lookup_fn lookup);
bool m27_identity_guest_image(const void *address, m27_identity_info *info);
#endif
