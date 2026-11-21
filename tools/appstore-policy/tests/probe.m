#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <assert.h>
#include <dlfcn.h>
#include <string.h>

extern void m27_store_test_start(unsigned);
extern void *m27_store_test_load(const char *);
extern unsigned m27_store_test_process(const char *);
extern unsigned m27_store_test_installed(void);
extern BOOL m27_store_trace_test_install(void);

static BOOL readBool(id obj, const char *name) {
    return ((BOOL (*)(id, SEL))objc_msgSend)(obj, sel_registerName(name));
}
static void setInt(id obj, const char *name, NSInteger value) {
    ((void (*)(id, SEL, NSInteger))objc_msgSend)(obj, sel_registerName(name), value);
}

int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 3);
    assert(m27_store_test_process(NULL) == 0);
    assert(m27_store_test_process("probe") == 0);
    assert(m27_store_test_process("appstored") == 1);
    assert(m27_store_test_process("appstoreagent") == 1);
    assert(m27_store_test_process("App Store") == 2);
    assert(m27_store_test_installed() == 0);
    assert(!m27_store_trace_test_install());
    BOOL early = !strcmp(argv[2], "early");
    BOOL wrong = !strcmp(argv[2], "wrong");
    BOOL missing = !strcmp(argv[2], "missing");
    if (!early) m27_store_test_start(3);
    assert(m27_store_test_installed() == 0);
    if (missing) { puts("PASS missing classes"); return 0; }
    void *handle = early ? dlopen(argv[1], RTLD_NOW | RTLD_LOCAL) : m27_store_test_load(argv[1]);
    if (!handle) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    BOOL (*loadSaw)(void) = dlsym(handle, "fixture_load_saw_capability");

    assert(loadSaw && !loadSaw());
    if (early) {
        assert(m27_store_test_installed() == 0);
        m27_store_test_start(3);
    }
    assert(m27_store_test_installed() == (wrong ? 2 : 3));
    id device = [objc_lookUpClass("Device") new];
    id purchase = [objc_lookUpClass("PurchaseInfo") new];
    assert(readBool(device, "isAppleSiliconMac") == !wrong);
    assert(readBool(objc_lookUpClass("ASKMobileGestalt"), "isAppleSiliconMac"));
    if (!strcmp(argv[2], "wronggate")) {
        assert(!m27_store_trace_test_install());
        puts("PASS trace refuses wrong ABI");
        return 0;
    }
    assert(m27_store_trace_test_install());
    assert(m27_store_trace_test_install());
    unsigned (*gateCalls)(void) = dlsym(handle, "fixture_gate_calls");
    assert(gateCalls && gateCalls() == 0);
    BOOL (*gate)(id, SEL, id, BOOL) = (void *)objc_msgSend;
    SEL selector = sel_registerName("hasCapabilitiesIncludingCompatibilityCheck:isVisionOSCompatibleIOSApp:");
    Class gestalt = objc_lookUpClass("ASKMobileGestalt");
    assert(gate(gestalt, selector, @[@"arm64"], NO));
    assert(!gate(gestalt, selector, @[@"gyroscope"], NO));
    assert(gate(gestalt, selector, @[@"gyroscope"], YES));
    assert(!gate(gestalt, selector, nil, NO));
    assert(gateCalls() == 4);
    for (NSInteger platform = 0; platform < 5; platform++) {
        setInt(purchase, "setExpectedSoftwarePlatform:", platform);
        for (NSInteger original = 0; original < 2; original++) {
            setInt(purchase, "setRequireUniversal:", original);
            assert(readBool(purchase, "requireUniversal") ==
                (original || (!wrong && platform == 2)));
        }
    }

    m27_store_test_start(3);
    setInt(purchase, "setExpectedSoftwarePlatform:", 1);
    setInt(purchase, "setRequireUniversal:", 0);
    assert(!readBool(purchase, "requireUniversal"));
    puts("PASS policy, load timing, ABI guards, repeat installation");
} }
