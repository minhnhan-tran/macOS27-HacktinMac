#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <os/log.h>
#include <stdlib.h>
#include <string.h>

static IMP originalUniversal;
static unsigned policy;
static BOOL daemonInstalled, catalogInstalled;
enum { Daemon = 1, Catalog = 2 };

static BOOL capable(id self, SEL cmd) { return YES; }
static BOOL universal(id self, SEL cmd) {
    NSInteger platform = ((NSInteger (*)(id, SEL))objc_msgSend)(
        self, sel_registerName("expectedSoftwarePlatform"));

    if (platform == 2) return YES;
    return ((BOOL (*)(id, SEL))originalUniversal)(self, cmd);
}

static BOOL getter(Method m, const char *type) {
    if (!m || method_getNumberOfArguments(m) != 2) return NO;
    char *actual = method_copyReturnType(m);
    BOOL valid = actual && (type ? !strcmp(actual, type) :
        (!strcmp(actual, "c") || !strcmp(actual, "B")));
    free(actual);
    return valid;
}

static void install(void) {
    if ((policy & Daemon) && !daemonInstalled) {
        Class d = objc_lookUpClass("Device"), p = objc_lookUpClass("PurchaseInfo");
        Method cap = class_getInstanceMethod(d, sel_registerName("isAppleSiliconMac"));
        Method uni = class_getInstanceMethod(p, sel_registerName("requireUniversal"));
        Method plat = class_getInstanceMethod(p, sel_registerName("expectedSoftwarePlatform"));

        if (getter(cap, NULL) && getter(uni, NULL) && getter(plat, @encode(NSInteger))) {
            originalUniversal = method_getImplementation(uni);
            method_setImplementation(uni, (IMP)universal);
            method_setImplementation(cap, (IMP)capable);
            daemonInstalled = YES;
        }
    }
    if ((policy & Catalog) && !catalogInstalled) {
        Class c = objc_lookUpClass("ASKMobileGestalt");
        Method m = class_getClassMethod(c, sel_registerName("isAppleSiliconMac"));
        if (getter(m, NULL)) {
            method_setImplementation(m, (IMP)capable);
            catalogInstalled = YES;
        }
    }
}

static unsigned policyForProcess(const char *name) {
    if (!name) return 0;
    if (!strcmp(name, "appstoreagent") || !strcmp(name, "appstored")) return Daemon;
    if (!strcmp(name, "App Store")) return Catalog;
    return 0;
}

__attribute__((constructor)) static void start(void) {
    policy = policyForProcess(getprogname());
    if (policy & Catalog) {

        if (!dlopen("/System/Library/PrivateFrameworks/AppStoreKit.framework/AppStoreKit",
                    RTLD_LAZY | RTLD_LOCAL)) {
            os_log_error(OS_LOG_DEFAULT, "M27 Store policy: AppStoreKit load failed");
            return;
        }
    }
    if (policy) {
        install();
        os_log(OS_LOG_DEFAULT, "M27 Store policy: selected=%u daemon=%d catalog=%d",
               policy, (int)daemonInstalled, (int)catalogInstalled);
    }
}

#ifdef M27_STORE_POLICY_TEST

void m27_store_test_start(unsigned selected) {
    policy = selected;
    install();
}
void *m27_store_test_load(const char *path) {
    void *handle = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
    if (handle) install();
    return handle;
}
unsigned m27_store_test_process(const char *name) { return policyForProcess(name); }
unsigned m27_store_test_installed(void) {
    return (daemonInstalled ? Daemon : 0) | (catalogInstalled ? Catalog : 0);
}
#endif
