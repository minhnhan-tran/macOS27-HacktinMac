/*
 * Fuzz target: the interface-unit parser (src/ifunit.c). Units are loaded
 * only from trusted directories, but they are code-like input (they decide
 * how native calls are made), so the parser must reject anything malformed
 * without reading outside the buffer. Each input is checked as it is, then
 * again with its payload hash recomputed so that mutations reach the record
 * rules behind the hash. A valid unit must find every one of its records by
 * name.
 */
#include "m27/bridge.h"
#include <stdlib.h>
#include <string.h>

static void run(const uint8_t *buf, size_t size)
{
    m27_if_view v;
    char why[256];
    if (m27_ifunit_check(buf, size, NULL, &v, why, sizeof why) != 0) {
        if (!why[0]) abort();
        return;
    }
    for (uint32_t k = 0; k < v.nrecs; k++)
        if (m27_ifunit_find(&v, v.str + v.recs[k].name) != &v.recs[k]) abort();
    if (m27_ifunit_find(&v, "\x7f no such symbol")) abort();
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (size > (1u << 20)) return 0;
    /* an exactly sized, aligned copy: ASan sees any read past the end */
    uint8_t *b = malloc(size ? size : 1);
    memcpy(b, data, size);
    run(b, size);
    if (size >= sizeof(m27_if_hdr)) {
        uint64_t h = m27_aot_hash64(b + sizeof(m27_if_hdr), size - sizeof(m27_if_hdr), 0);
        memcpy(b + offsetof(m27_if_hdr, payload_hash), &h, 8);
        run(b, size);
    }
    free(b);
    return 0;
}
