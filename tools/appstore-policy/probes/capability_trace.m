#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <os/log.h>
#include <stdlib.h>
#include <string.h>

static IMP original;

static BOOL trace(id self, SEL cmd, id capabilities, BOOL visionCompatible) {
    BOOL accepted = ((BOOL (*)(id, SEL, id, BOOL))original)(
        self, cmd, capabilities, visionCompatible);

    if ([capabilities isKindOfClass:[NSArray class]]) {
        NSUInteger count = [capabilities count];
        os_log(OS_LOG_DEFAULT, "M27 Store gate: accepted=%d vision=%d count=%lu",
               (int)accepted, (int)visionCompatible, (unsigned long)count);
        NSCharacterSet *allowed = [NSCharacterSet characterSetWithCharactersInString:
            @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."];
        for (NSUInteger i = 0; i < MIN(count, 64); i++) {
            id key = capabilities[i];
            if ([key isKindOfClass:[NSString class]] && [key length] <= 128 &&
                [key rangeOfCharacterFromSet:allowed.invertedSet].location == NSNotFound) {
                os_log(OS_LOG_DEFAULT, "M27 Store gate: accepted=%d capability=%{public}s",
                       (int)accepted, [key UTF8String]);
            }
        }
    }
    return accepted;
}

static BOOL hasType(Method method, unsigned argument, const char *expected) {
    char *actual = argument == 0 ? method_copyReturnType(method) :
        method_copyArgumentType(method, argument);
    BOOL valid = actual && (expected ? !strcmp(actual, expected) :
        (!strcmp(actual, "c") || !strcmp(actual, "B")));
    free(actual);
    return valid;
}

static BOOL install(void) {
    if (original) return YES;
    Method method = class_getClassMethod(objc_lookUpClass("ASKMobileGestalt"),
        sel_registerName("hasCapabilitiesIncludingCompatibilityCheck:isVisionOSCompatibleIOSApp:"));
    if (!method || method_getNumberOfArguments(method) != 4 ||
        !hasType(method, 0, NULL) || !hasType(method, 2, "@") || !hasType(method, 3, NULL))
        return NO;
    original = method_getImplementation(method);
    method_setImplementation(method, (IMP)trace);
    return YES;
}

#ifdef M27_STORE_POLICY_TEST
BOOL m27_store_trace_test_install(void) { return install(); }
#else
__attribute__((constructor)) static void start(void) {
    if (strcmp(getprogname(), "App Store")) return;
    if (!dlopen("/System/Library/PrivateFrameworks/AppStoreKit.framework/AppStoreKit",
                RTLD_NOW | RTLD_LOCAL)) return;
    os_log(OS_LOG_DEFAULT, "M27 Store gate: trace installed=%d", (int)install());
}
#endif
