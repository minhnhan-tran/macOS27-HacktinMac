// Quiet native26A428 E5 policy, derived from trace_tensor_input.cpp and
// trace_allocation.mm. Only TextRecognition compile paths select GPU or redirect.
// 26B131: every e5rt_* function used here is in x86_64 Espresso (dyld_shared_cache_x86_64, 27.2.1).
#import <Foundation/Foundation.h>
#import <IOSurface/IOSurface.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>
#include <sys/stat.h>
#include <mutex>
#include <atomic>
#include <cerrno>
#include <fts.h>
#include <sys/acl.h>
#include <os/log.h>
extern "C" {
void m27_live_text_record_library(void*,int);           // libm27_input_coherence (linked)
void m27_live_text_set_recognition_active(void);         // libLiveTextConfig (linked)
int e5rt_e5_compiler_compile(void*,void*,void*,void*) __attribute__((weak_import));
int e5rt_e5_compiler_options_get_compute_device_types_mask(void*,uint64_t*) __attribute__((weak_import));
int e5rt_e5_compiler_options_set_compute_device_types_mask(void*,uint64_t) __attribute__((weak_import));
int e5rt_e5_compiler_create(void**) __attribute__((weak_import));
int e5rt_e5_compiler_create_with_config(void**,void*) __attribute__((weak_import));
int e5rt_e5_compiler_config_options_create(void**) __attribute__((weak_import));
int e5rt_e5_compiler_config_options_set_cache_bundle_location(void*,const char*) __attribute__((weak_import));
int e5rt_e5_compiler_config_options_release(void**) __attribute__((weak_import));
}
static const char *root(){const char *p=getenv("M27_LIVE_TEXT_ROOT");return p&&*p&&strcmp(p,"0")?p:nullptr;}
static bool nonmemref_ready=false;
typedef id __attribute__((ns_returns_retained)) (*InitFunction)(id __attribute__((ns_consumed)),SEL);
static InitFunction original_init;
static signed char (*original_getter)(id,SEL);
static signed char nonmemref(id self,SEL selector){return root()?0:original_getter(self,selector);}
static id __attribute__((ns_returns_retained)) descriptor_init(id __attribute__((ns_consumed)) self,SEL selector){
 id result=original_init(self,selector);
 if(result&&root())reinterpret_cast<void(*)(id,SEL,signed char)>(objc_msgSend)(result,sel_registerName("setEnableMemrefBufferization:"),0);
 return result;
}
static bool type_is(Method m,unsigned i,const char *want){char *s=method_copyArgumentType(m,i);bool ok=s&&!strcmp(s,want);free(s);return ok;}
static bool signature(Method m,const char *result,unsigned count,const char *last=nullptr){
 if(!m||method_getNumberOfArguments(m)!=count)return false;char *s=method_copyReturnType(m);bool ok=s&&!strcmp(s,result);free(s);
 return ok&&type_is(m,0,"@")&&type_is(m,1,":")&&(!last||type_is(m,count-1,last));
}
static void replace_local(Class cls,SEL selector,Method old,IMP replacement){
 // Do not replace an inherited NSObject method globally.
 if(!class_addMethod(cls,selector,replacement,method_getTypeEncoding(old)))method_setImplementation(class_getInstanceMethod(cls,selector),replacement);
}
static void install_nonmemref(){
 if(!root())return;
 dlopen("/System/Library/Frameworks/MetalPerformanceShadersGraph.framework/MetalPerformanceShadersGraph",RTLD_LAZY|RTLD_LOCAL);
 Class cls=objc_getClass("MPSGraphCompilationDescriptor");SEL get=sel_registerName("enableMemrefBufferization"),set=sel_registerName("setEnableMemrefBufferization:"),init=sel_registerName("init");
 Method gm=class_getInstanceMethod(cls,get),sm=class_getInstanceMethod(cls,set),im=class_getInstanceMethod(cls,init);
 if(!signature(gm,"c",2)||!signature(sm,"v",3,"c")||!signature(im,"@",2))return;
 original_getter=reinterpret_cast<signed char(*)(id,SEL)>(method_getImplementation(gm));original_init=reinterpret_cast<InitFunction>(method_getImplementation(im));
 replace_local(cls,get,gm,reinterpret_cast<IMP>(nonmemref));replace_local(cls,init,im,reinterpret_cast<IMP>(descriptor_init));nonmemref_ready=true;
}
static bool text_model(const char *path){return path&&strstr(path,"/TextRecognition.framework/");}
static bool detector_model(const char *path){
 if(!text_model(path))return false;
 const char suffix[]="/cr_td_model_v3_e5.mlmodelc.bundle/model.mil";size_t n=strlen(path),s=sizeof(suffix)-1;return n>=s&&!strcmp(path+n-s,suffix);
}
// e5rt replaces an incomplete cache entry with remove_all(); a folder carrying a deny-delete ACE (seen on the HD 4000:
// empty entries in mediaanalysisd's container, every folder `group:everyone deny delete`) makes that EACCES, e5rt
// throws, and every later compile fails with Code 11. Only the deny-delete entries are removed, only below e5rt's
// own cache folder (M27_LIVE_TEXT_E5_CACHE, else <Caches>/<bundle id or program name>/com.apple.e5rt.e5bundlecache).
static os_log_t trace(){static os_log_t log=os_log_create("com.m27.live-text","e5-cache");return log;}
static bool diagnostics(){const char *v=getenv("M27_LIVE_TEXT_DIAGNOSTICS");return v&&!strcmp(v,"1");}
static bool deny_delete(acl_entry_t entry){
 acl_tag_t tag;acl_permset_t perms;
 return !acl_get_tag_type(entry,&tag)&&tag==ACL_EXTENDED_DENY&&!acl_get_permset(entry,&perms)&&acl_get_perm_np(perms,ACL_DELETE)==1;
}
static bool drop_deny_delete(const char *path,int *error){
 acl_t acl=acl_get_link_np(path,ACL_TYPE_EXTENDED);if(!acl)return false;
 bool changed=false;
 for(bool found=true;found;){   // acl_delete_entry ends an iteration: rescan from the first entry after each removal
  found=false;acl_entry_t entry;
  for(int id=ACL_FIRST_ENTRY;acl_get_entry(acl,id,&entry)==0;id=ACL_NEXT_ENTRY)
   if(deny_delete(entry)){found=!acl_delete_entry(acl,entry);changed|=found;break;}
 }
 if(changed&&acl_set_link_np(path,ACL_TYPE_EXTENDED,acl))*error=errno;
 acl_free(acl);return changed;
}
static unsigned clear_cache_folder(const std::string &folder){
 struct stat st;if(lstat(folder.c_str(),&st)||!S_ISDIR(st.st_mode))return 0;
 char *paths[]={const_cast<char*>(folder.c_str()),nullptr};
 FTS *walk=fts_open(paths,FTS_PHYSICAL|FTS_NOCHDIR,nullptr);if(!walk)return 0;
 unsigned cleared=0,failed=0;int first_error=0;
 while(FTSENT *e=fts_read(walk)){
  if(e->fts_level<1||e->fts_info==FTS_DP)continue;
  int error=0;if(!drop_deny_delete(e->fts_path,&error))continue;
  if(error){failed++;if(!first_error)first_error=error;}else cleared++;
 }
 fts_close(walk);
 if(!cleared&&!failed)return 0;
 os_log_error(trace(),"deny-delete ACEs under %{public}s: removed %u, failed %u (errno %d)",folder.c_str(),cleared,failed,first_error);
 if(diagnostics())fprintf(stderr,"M27_LIVE_TEXT_DIAGNOSTIC event=e5-cache-acl removed=%u failed=%u errno=%d\n",cleared,failed,first_error);
 return cleared;
}
static unsigned clear_e5_cache(){
 const char *configured=getenv("M27_LIVE_TEXT_E5_CACHE");
 if(configured&&*configured)return clear_cache_folder(configured);
 unsigned cleared=0;
 @autoreleasepool{
  NSString *caches=NSSearchPathForDirectoriesInDomains(NSCachesDirectory,NSUserDomainMask,YES).firstObject;if(!caches)return 0;
  NSString *identifier=NSBundle.mainBundle.bundleIdentifier,*program=@(getprogname());
  for(NSString *name in identifier&&![identifier isEqualToString:program]?@[identifier,program]:@[program])
   cleared+=clear_cache_folder([[caches stringByAppendingPathComponent:name] stringByAppendingPathComponent:@"com.apple.e5rt.e5bundlecache"].fileSystemRepresentation);
 }
 return cleared;
}
struct MaskRestore {void *options;uint64_t previous;bool restored=false;~MaskRestore(){if(!restored)e5rt_e5_compiler_options_set_compute_device_types_mask(options,previous);}int restore(){restored=true;return e5rt_e5_compiler_options_set_compute_device_types_mask(options,previous);}};
extern "C" int live_text_compile(void *compiler,void *model,void *options,void *out){
 const char *path=static_cast<const char*>(model),*package=root();if(!package||!text_model(path))return e5rt_e5_compiler_compile(compiler,model,options,out);
 // Loading Metal alone must not initialize MPSGraph. Prepare this legacy
 // backend policy only once an actual recognition compile needs it, before
 // that compile can create descriptors on its worker threads.
 static std::once_flag prepare;
 std::call_once(prepare,install_nonmemref);
 if(!nonmemref_ready)return 22;
 m27_live_text_set_recognition_active();
 std::string replacement;
 if(detector_model(path)){if(package[0]!='/')return 22;replacement=std::string(package)+"/detector/model.mil";if(access(replacement.c_str(),R_OK))return 22;}
 uint64_t previous=0;if(e5rt_e5_compiler_options_get_compute_device_types_mask(options,&previous))return 22;MaskRestore restore{options,previous};
 if(e5rt_e5_compiler_options_set_compute_device_types_mask(options,2))return 22;
 void *input=replacement.empty()?model:const_cast<char*>(replacement.c_str());
 static std::once_flag cache_checked;
 std::call_once(cache_checked,clear_e5_cache);
 int result=e5rt_e5_compiler_compile(compiler,input,options,out);
 if(result){
  // A deny-delete ACE that appeared after the first check: clear it and compile once more.
  os_log_error(trace(),"compile of %{public}s failed (%d)",path,result);
  if(clear_e5_cache()){result=e5rt_e5_compiler_compile(compiler,input,options,out);os_log_error(trace(),"retry: %d",result);}
 }
 if(!result&&out&&*static_cast<void**>(out))
     m27_live_text_record_library(*static_cast<void**>(out),detector_model(path)?2:1);
 int restored=restore.restore();return result?result:(restored?22:0);
}
extern "C" int live_text_compiler_create(void **out){
 const char *cache=getenv("M27_LIVE_TEXT_E5_CACHE");if(!root()||!cache||!*cache)return e5rt_e5_compiler_create(out);
 void *config=nullptr;int result=e5rt_e5_compiler_config_options_create(&config);
 if(!result)result=e5rt_e5_compiler_config_options_set_cache_bundle_location(config,cache);
 if(!result)result=e5rt_e5_compiler_create_with_config(out,config);
 if(config)e5rt_e5_compiler_config_options_release(&config);return result;
}
#define HOOK(replacement,original) __attribute__((used,section("__DATA,__interpose"))) static const struct {const void *r,*o;} hook_##original={(void*)replacement,(void*)original};
HOOK(live_text_compile,e5rt_e5_compiler_compile)
HOOK(live_text_compiler_create,e5rt_e5_compiler_create)
