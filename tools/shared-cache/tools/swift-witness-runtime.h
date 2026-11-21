#ifndef SSD_SWIFT_WITNESS_RUNTIME_H
#define SSD_SWIFT_WITNESS_RUNTIME_H
#include <stdint.h>
#include <stddef.h>
typedef const void *WitnessOriginal(const void *, const void *, const void *const *);
typedef const void *WitnessLookup(const void *, const void *, size_t, const void **);
struct WitnessRuntime {
  const intptr_t *once;
  void (*initialize)(void);
  const uint32_t *mode;
  const void *table;
  WitnessLookup *lookup;
  WitnessOriginal *original;
};
extern struct WitnessRuntime m27_witness_runtime;
const void *m27_witness_lookup(const void *, const void *, const void *const *);
typedef const void *MetatypeOriginal(const void *);
struct MetatypeRuntime {
  const intptr_t *once;
  void (*initialize)(void);
  const uint32_t *mode;
  const void *table;
  WitnessLookup *lookup;
  const void *key;
  MetatypeOriginal *original;
};
extern struct MetatypeRuntime m27_metatype_runtime;
const void *m27_metatype_lookup(const void *);
struct MetadataShape {
  const void *descriptor;
  uint32_t types, count;
};
typedef const void *MetadataOriginal(const void *, const void *const *);
struct MetadataRuntime {
  const uint32_t *mode;
  const void *table;
  WitnessLookup *lookup;
  MetadataOriginal *original;
  const struct MetadataShape *shapes;
  size_t count;
};
extern struct MetadataRuntime m27_metadata_runtime;
const void *m27_metadata_lookup(const void *, const void *const *);
#endif
