/* Link-time stand-in for /usr/lib/libCoreRepairState.dylib, which exists
 * only in the arm64e shared cache. It defines the exported class the test
 * binds and subclasses. Never installed: at run time dyld (native) and m27
 * both take the cache's library. */
#import <Foundation/Foundation.h>
@interface CRRepairHistoryItem : NSObject
@end
@implementation CRRepairHistoryItem
@end
