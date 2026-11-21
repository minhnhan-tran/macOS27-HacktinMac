// hd4000-planes: the HD 4000 Metal driver reads and writes a texture of plane N > 0 of a planar IOSurface with plane 0's
// row pitch (diag#0025/#0024; research's probe phases C and D on laptop 1: 444f and 420f-with-padded-plane-1 wrong,
// 444f-with-padded-plane-0 and 420f right). Loaded into every process that loads the driver (LC_LOAD_WEAK_DYLIB in the
// driver), it answers -newTextureWithDescriptor:iosurface:plane: for such a plane with a "shadow": an ordinary managed
// texture of the same descriptor, kept in step with the surface's plane on the CPU, where the pitch is the surface's own:
//   - filled from the plane when made, and again before a command buffer is committed whenever the surface's seed moved
//     (another writer, in this process or another, changed it);
//   - when made with ShaderWrite or RenderTarget usage, copied back into the plane after every command buffer that
//     completes while it is alive and whose result differs (IOSurfaceLock/Unlock, so the seed moves and readers in
//     other processes, e.g. WindowServer, refill).
// -iosurface and -iosurfacePlane of a shadow answer the surface and plane, as the driver's own texture would.
// Planes whose pitch equals plane 0's (420f, 420v, most video) are left to the driver untouched.
// HD4000_PLANES=0 in a process's environment turns it off there; HD4000_PLANES_LOG=1 logs each shadow.
// HD4000_PLANES_HOST=1 (host tests only): hook the classes of MTLCreateSystemDefaultDevice instead of the HD 4000 ones.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <objc/runtime.h>
#import <os/log.h>
// HD4000_PLANES_LOG=1: each event to the unified log; =2: to stderr as well (host tests)
#define NOTE(...) do { os_log(gLog, __VA_ARGS__); if (gVerbose > 1) { fprintf(stderr, "hd4000-planes: "); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } } while (0)

static os_log_t gLog; static int gVerbose;
static IMP gOrigNewTex, gOrigCommit, gOrigIOSurface, gOrigIOSurfacePlane;
static NSMutableArray *gShadows; static NSLock *gLock; static char kShadowKey;

@interface HD4KShadow : NSObject { @public __weak id<MTLTexture> tex; IOSurfaceRef surf; NSUInteger plane; BOOL writes;
    uint32_t seed; uint64_t hash; size_t rowBytes; } @end
@implementation HD4KShadow - (void)dealloc { if (surf) CFRelease(surf); } @end

static uint64_t fnv(const uint8_t *b, size_t n) { uint64_t x = 1469598103934665603ULL; for (size_t i = 0; i < n; i++) x = (x ^ b[i]) * 1099511628211ULL; return x; }

static void fill(HD4KShadow *sh, id<MTLTexture> tex) {          // surface plane -> shadow
    uint32_t s0 = 0; if (IOSurfaceLock(sh->surf, kIOSurfaceLockReadOnly, &s0) != KERN_SUCCESS) return;
    void *base = IOSurfaceGetBaseAddressOfPlane(sh->surf, sh->plane); size_t bpr = IOSurfaceGetBytesPerRowOfPlane(sh->surf, sh->plane);
    size_t h = MIN(tex.height, IOSurfaceGetHeightOfPlane(sh->surf, sh->plane)), w = MIN(tex.width, IOSurfaceGetWidthOfPlane(sh->surf, sh->plane));
    if (base && w && h) [tex replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0 withBytes:base bytesPerRow:bpr];
    IOSurfaceUnlock(sh->surf, kIOSurfaceLockReadOnly, &s0); sh->seed = IOSurfaceGetSeed(sh->surf); }

static void store(HD4KShadow *sh, id<MTLTexture> tex) {         // shadow -> surface plane
    size_t w = MIN(tex.width, IOSurfaceGetWidthOfPlane(sh->surf, sh->plane)), h = MIN(tex.height, IOSurfaceGetHeightOfPlane(sh->surf, sh->plane));
    size_t rb = w * IOSurfaceGetBytesPerElementOfPlane(sh->surf, sh->plane); if (!rb || !h) return;
    NSMutableData *buf = [NSMutableData dataWithLength:rb * h];
    [tex getBytes:buf.mutableBytes bytesPerRow:rb fromRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0];
    uint64_t hs = fnv(buf.bytes, buf.length); if (hs == sh->hash) return;
    uint32_t s0 = 0; if (IOSurfaceLock(sh->surf, 0, &s0) != KERN_SUCCESS) return;
    uint8_t *base = IOSurfaceGetBaseAddressOfPlane(sh->surf, sh->plane); size_t bpr = IOSurfaceGetBytesPerRowOfPlane(sh->surf, sh->plane);
    for (size_t y = 0; base && y < h; y++) memcpy(base + y * bpr, (const uint8_t *)buf.bytes + y * rb, rb);
    IOSurfaceUnlock(sh->surf, 0, &s0); sh->hash = hs; sh->seed = IOSurfaceGetSeed(sh->surf);
    if (gVerbose) NOTE("stored shadow -> surface %u plane %lu seed %u", IOSurfaceGetID(sh->surf), (unsigned long)sh->plane, sh->seed); }

static BOOL needs_shadow(MTLTextureDescriptor *d, IOSurfaceRef s, NSUInteger plane) {
    if (!s || plane == 0 || IOSurfaceGetPlaneCount(s) <= plane || d.textureType != MTLTextureType2D || d.mipmapLevelCount > 1) return NO;
    return IOSurfaceGetBytesPerRowOfPlane(s, plane) != IOSurfaceGetBytesPerRowOfPlane(s, 0); }

// A -new… method returns its result retained (+1); the hook must too, or callers over-release it.
typedef id (*NewTexIMP)(id, SEL, id, IOSurfaceRef, NSUInteger) __attribute__((ns_returns_retained));
static id new_tex(id self, SEL _cmd, MTLTextureDescriptor *d, IOSurfaceRef s, NSUInteger plane) __attribute__((ns_returns_retained));
static id new_tex(id self, SEL _cmd, MTLTextureDescriptor *d, IOSurfaceRef s, NSUInteger plane) {
    if (!needs_shadow(d, s, plane)) return ((NewTexIMP)gOrigNewTex)(self, _cmd, d, s, plane);
    MTLTextureDescriptor *sd = [d copy]; sd.storageMode = MTLStorageModeManaged; sd.usage = d.usage | MTLTextureUsageShaderRead;
    id<MTLTexture> tex = [(id<MTLDevice>)self newTextureWithDescriptor:sd];
    if (!tex) return ((NewTexIMP)gOrigNewTex)(self, _cmd, d, s, plane);
    HD4KShadow *sh = [HD4KShadow new]; sh->tex = tex; sh->surf = (IOSurfaceRef)CFRetain(s); sh->plane = plane;
    sh->writes = (d.usage & (MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget)) != 0;
    objc_setAssociatedObject(tex, &kShadowKey, sh, OBJC_ASSOCIATION_RETAIN);
    fill(sh, tex);
    [gLock lock]; [gShadows addObject:sh]; [gLock unlock];
    if (gVerbose) NOTE("shadow for surface %u plane %lu (%zux%zu, pitch %zu vs plane 0 %zu) usage 0x%lx %s",
        IOSurfaceGetID(s), (unsigned long)plane, IOSurfaceGetWidthOfPlane(s, plane), IOSurfaceGetHeightOfPlane(s, plane),
        IOSurfaceGetBytesPerRowOfPlane(s, plane), IOSurfaceGetBytesPerRowOfPlane(s, 0), (unsigned long)d.usage, sh->writes ? "written" : "read");
    return tex; }

static void commit_hook(id self, SEL _cmd) {
    NSArray *live = nil; [gLock lock]; if (gShadows.count) live = [gShadows copy]; [gLock unlock];
    if (live) { NSMutableArray *written = nil;
        for (HD4KShadow *sh in live) { id<MTLTexture> tex = sh->tex;
            if (!tex) { [gLock lock]; [gShadows removeObject:sh]; [gLock unlock]; continue; }
            if (sh->writes) { if (!written) written = [NSMutableArray array]; [written addObject:sh]; }
            else if (IOSurfaceGetSeed(sh->surf) != sh->seed) fill(sh, tex); }
        if (written) { id<MTLBlitCommandEncoder> blit = [(id<MTLCommandBuffer>)self blitCommandEncoder];
            for (HD4KShadow *sh in written) { id<MTLTexture> tex = sh->tex; if (tex) [blit synchronizeResource:tex]; }
            [blit endEncoding];
            [(id<MTLCommandBuffer>)self addCompletedHandler:^(id<MTLCommandBuffer> b) { (void)b;
                for (HD4KShadow *sh in written) { id<MTLTexture> tex = sh->tex; if (tex) store(sh, tex); } }]; } }
    ((void (*)(id, SEL))gOrigCommit)(self, _cmd); }

static IOSurfaceRef iosurface_hook(id self, SEL _cmd) { HD4KShadow *sh = objc_getAssociatedObject(self, &kShadowKey);
    return sh ? sh->surf : ((IOSurfaceRef (*)(id, SEL))gOrigIOSurface)(self, _cmd); }
static NSUInteger iosurface_plane_hook(id self, SEL _cmd) { HD4KShadow *sh = objc_getAssociatedObject(self, &kShadowKey);
    return sh ? sh->plane : ((NSUInteger (*)(id, SEL))gOrigIOSurfacePlane)(self, _cmd); }

static int hook_classes(Class device, Class buffer, Class texture) {
    Method m = class_getInstanceMethod(device, @selector(newTextureWithDescriptor:iosurface:plane:));
    Method c = class_getInstanceMethod(buffer, @selector(commit));
    if (!m || !c) return 0;
    gOrigNewTex = method_setImplementation(m, (IMP)new_tex); gOrigCommit = method_setImplementation(c, (IMP)commit_hook);
    Method i = class_getInstanceMethod(texture, @selector(iosurface)), p = class_getInstanceMethod(texture, @selector(iosurfacePlane));
    if (i) gOrigIOSurface = method_setImplementation(i, (IMP)iosurface_hook);
    if (p) gOrigIOSurfacePlane = method_setImplementation(p, (IMP)iosurface_plane_hook);
    NOTE("hd4000-planes on %s / %s / %s", class_getName(device), class_getName(buffer), class_getName(texture));
    return 1; }

__attribute__((constructor)) static void hd4000_planes_init(void) {
    const char *off = getenv("HD4000_PLANES"); if (off && !strcmp(off, "0")) return;
    gLog = os_log_create("org.m27.hd4000-planes", "planes"); gVerbose = getenv("HD4000_PLANES_LOG") ? atoi(getenv("HD4000_PLANES_LOG")) : 0;
    gShadows = [NSMutableArray array]; gLock = [NSLock new];
    if (getenv("HD4000_PLANES_HOST")) {   // host tests: the classes of this Mac's device (the HD 4000 ones do not exist here)
        @autoreleasepool {
            id<MTLDevice> dev = MTLCreateSystemDefaultDevice(); id<MTLCommandBuffer> cb = [[dev newCommandQueue] commandBuffer];
            MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:4 height:4 mipmapped:NO];
            hook_classes([(id)dev class], [(id)cb class], [(id)[dev newTextureWithDescriptor:d] class]); }
        return; }
    Class dev = objc_getClass("MTLIGAccelDevice"), buf = objc_getClass("MTLIGAccelCommandBuffer"), tex = objc_getClass("MTLIGAccelTexture");
    if (dev && buf && tex) hook_classes(dev, buf, tex); }
