// Ported from archive proposals/swift-metadata-expansion/witness-capability.c.
// Only this helper's debug-library selection changes; no DYLD insertion/override.
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <uuid/uuid.h>
#include "witness-profile.h"

struct Data { uint32_t major,minor; const void *names,*disabled,*metadata;
              uintptr_t options; const void *descriptors,*witnesses; };
_Static_assert(__builtin_offsetof(struct Data,witnesses)==48,"witness map ABI");
static uintptr_t cache_base(void) {
    size_t length=0;
    const void *(*range)(size_t*)=dlsym(RTLD_DEFAULT,"_dyld_get_shared_cache_range");
    _Bool (*getuuid)(uuid_t)=dlsym(RTLD_DEFAULT,"_dyld_get_shared_cache_uuid");
    uuid_t actual,expected;
    if(!range || !getuuid || !getuuid(actual) || uuid_parse(CACHE_UUID,expected) ||
       memcmp(actual,expected,16)) _exit(3);
    uintptr_t base=(uintptr_t)range(&length);
    if(!base || !length) _exit(3);
    return base;
}
#ifdef TABLE_LIBRARY
static const uintptr_t names[3]={1,0,0},empty[1]={0};
__attribute__((visibility("default")))
struct Data _swift_prespecializationsData={1,MINOR,names,0,empty,1,0,0};
__attribute__((constructor)) static void initialize(void) {
    _swift_prespecializationsData.witnesses=(void*)(cache_base()+TABLE_OFFSET);
}
#else
typedef const void * __attribute__((swiftcall)) Resolve(const char*,size_t,const void*,const void *const*);
typedef const uintptr_t *Witness(const void*,const void*,const void *const*);
typedef const void *Lookup(const void*,const void*,size_t,const void**);
__attribute__((noinline,visibility("default"))) void m27_witness_phase(unsigned phase) {
    __asm__ volatile("" : : "r"(phase) : "memory");
}
int main(int argc,char **argv) {
    alarm(40); setbuf(stdout,NULL);
    if(argc!=2) return 2;
    uintptr_t base=cache_base();
    const struct Data *data=(const void*)(base+DATA_OFFSET);
    if(strcmp(argv[1],"native")) {
        // Set this before libswiftCore loads, only in the temporary helper.
        // Putting it on DTrace also makes the tracer try to load the test dylib.
        if(setenv("SWIFT_DEBUG_LIB_PRESPECIALIZED_PATH",argv[1],1)) return 4;
        void *library=dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL);
        if(!library) { fprintf(stderr,"%s\n",dlerror()); return 4; }
        data=dlsym(library,"_swift_prespecializationsData");
        if(!data) return 4;
    }
    void *core=dlopen("/usr/lib/swift/libswiftCore.dylib",RTLD_NOW|RTLD_LOCAL);
    Resolve *resolve=dlsym(core,"swift_getTypeByMangledNameInContext");
    Witness *witness=dlsym(core,"swift_getWitnessTable");
    Lookup *lookup=dlsym(RTLD_DEFAULT,"_dyld_find_pointer_hash_table_entry");
    _Bool (*overridden)(void)=dlsym(RTLD_DEFAULT,"dyld_shared_cache_some_image_overridden");
    if(!core || !resolve || !witness || !lookup || !overridden) return 5;
    const void *type=resolve("s6UInt32V",strlen("s6UInt32V"),NULL,NULL);
    const void *optional=resolve("s6UInt32VSg",strlen("s6UInt32VSg"),NULL,NULL);
    const uintptr_t *root[2]={dlsym(core,"$ss6UInt32VSQsWP"),dlsym(core,"$ss6UInt32VSHsWP")};
    const void *conformances[2]={dlsym(core,"$sxSgSQsSQRzlMc"),dlsym(core,"$sxSgSHsSHRzlMc")};
    if(!type || !optional || !root[0] || !root[1] || !conformances[0] || !conformances[1]) return 6;
    unsigned mode=*(const unsigned*)(base+MODE_OFFSET);
    printf("{\"start\":true,\"cache_uuid\":\"%s\",\"minor\":%u,\"mode\":%u,\"override\":%s}\n",
           CACHE_UUID,data->minor,mode,overridden()?"true":"false");
    const void *args[sizeof(positive_key)/sizeof(*positive_key)];
    for(unsigned i=1;i<sizeof(positive_key)/sizeof(*positive_key);i++) args[i-1]=(void*)(base+positive_key[i]);
    m27_witness_phase(1);
    const uintptr_t *results[8];
    for(unsigned i=0;i<8;i++) {
        unsigned which=i%4,j=which%2;
        const void *conformance=which<2?(const void*)root[j][0]:conformances[j];
        results[i]=witness(conformance,which<2?type:optional,which<2?NULL:(const void*const*)&root[j]);
        if(!results[i] || results[i][0]!=(uintptr_t)conformance ||
           (which<2 && results[i]!=root[j]) || (i>=4 && results[i]!=results[i-4])) return 7;
    }
    if(results[3][1]!=(uintptr_t)results[2]) return 8;
    m27_witness_phase(2);
    for(unsigned i=0;i<2;i++)
        if(lookup((void*)(base+TABLE_OFFSET),(void*)(base+positive_key[0]),
                  sizeof(positive_key)/sizeof(*positive_key)-1,args)!=(void*)(base+POSITIVE_VALUE)) return 9;
    m27_witness_phase(3);
    printf("{\"complete\":true,\"requests\":8,\"positive_hits\":2,\"mode\":%u,\"override\":%s}\n",
           *(const unsigned*)(base+MODE_OFFSET),overridden()?"true":"false");
    return 0;
}
#endif
