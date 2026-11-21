// Backport the precomputed-witness lookup to pointer-map Swift runtimes.
// The generator resolves the target's existing initialization/mode state; the
// original runtime keeps ownership of environment and image-override policy.
#include "swift-witness-runtime.h"
struct WitnessRuntime m27_witness_runtime;
const void *m27_witness_lookup(const void *conformance,const void *type,
                                const void *const *arguments) {
  const struct WitnessRuntime *runtime=&m27_witness_runtime;
  if (__atomic_load_n(runtime->once,__ATOMIC_ACQUIRE)!=-1)
    runtime->initialize();
  // 2 is the native shared-cache pointer-map mode in the profiled ABI. Debug,
  // disabled (including image overrides), and name-map modes keep native work.
  if (__atomic_load_n(runtime->mode,__ATOMIC_RELAXED)==2) {
    const void *rest[1]={type};
    const void *found=runtime->lookup(runtime->table,conformance,1,rest);
    if (found) return found;
  }
  return runtime->original(conformance,type,arguments);
}

// Ordinary metatypes are immutable after construction and have no generic
// completion-state entry. The constructor symbol is a disjoint pointer-map key
// prefix; the instance metadata is its sole dependency. Reuse the runtime's
// existing image-override policy, exactly as for the witness map.
struct MetatypeRuntime m27_metatype_runtime;
const void *m27_metatype_lookup(const void *instance) {
  const struct MetatypeRuntime *runtime=&m27_metatype_runtime;
  if (__atomic_load_n(runtime->once,__ATOMIC_ACQUIRE)!=-1)
    runtime->initialize();
  if (__atomic_load_n(runtime->mode,__ATOMIC_RELAXED)==2) {
    const void *rest[1]={instance};
    const void *found=runtime->lookup(runtime->table,runtime->key,1,rest);
    if (found) return found;
  }
  return runtime->original(instance);
}

// MetadataCacheKey compares witness arguments by conformance descriptor, not
// allocation address (Swift MetadataCache.h, areWitnessTablesEqual). Preserve
// the original exact-pointer fast path and policy; only its misses consult this
// secondary map. Unsupported descriptors and distinct conformances still miss.
struct MetadataRuntime m27_metadata_runtime;
const void *m27_metadata_lookup(const void *descriptor,const void *const *arguments) {
  const struct MetadataRuntime *runtime=&m27_metadata_runtime;
  const void *found=runtime->original(descriptor,arguments);
  if (found || __atomic_load_n(runtime->mode,__ATOMIC_RELAXED)!=2) return found;
  size_t low=0,high=runtime->count;
  while (low<high) {
    size_t mid=low+(high-low)/2;
    const struct MetadataShape *shape=&runtime->shapes[mid];
    if ((uintptr_t)shape->descriptor<(uintptr_t)descriptor) low=mid+1;
    else high=mid;
  }
  if (low==runtime->count || runtime->shapes[low].descriptor!=descriptor) return 0;
  const struct MetadataShape *shape=&runtime->shapes[low];
  const void *key[63]; // The catalog ABI supports at most 63 scalar key arguments.
  for (uint32_t i=0;i<shape->count;i++)
    key[i]=i<shape->types ? arguments[i] : *(const void *const *)arguments[i];
  return runtime->lookup(runtime->table,descriptor,shape->count,key);
}
