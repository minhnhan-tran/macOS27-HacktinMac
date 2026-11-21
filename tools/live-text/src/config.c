// Process-local Live Text settings, set before the functional helpers' constructors (they depend on this image).
// No launchd job, user preference or per-user cache is created: Espresso keeps its ordinary cache unless a
// diagnostic run supplies M27_LIVE_TEXT_E5_CACHE itself. Shaders compile in the image's MTLCompilerService.
#include <stdlib.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#ifndef LIVE_TEXT_ROOT
#error LIVE_TEXT_ROOT must name the installed runtime folder
#endif
static void configure_once(void) {
    setenv("M27_LIVE_TEXT_ROOT", LIVE_TEXT_ROOT, 1);
    setenv("VISION_MPSNDARRAY_LIBRARY", LIVE_TEXT_ROOT "/MPSNDArray.metallib", 1);
}
void m27_live_text_configure(void) {
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    pthread_once(&once, configure_once);
}
__attribute__((constructor)) static void configure(void) { m27_live_text_configure(); }
// Set by libLiveTextPolicy once a TextRecognition compile is prepared; read by libm27_input_coherence. Kept here, the
// library both link, so each imports it from a named library (a folded shared-cache image cannot use flat lookups).
static atomic_bool recognition_active;
bool m27_live_text_recognition_active(void) { return atomic_load_explicit(&recognition_active, memory_order_acquire); }
void m27_live_text_set_recognition_active(void) { atomic_store_explicit(&recognition_active, true, memory_order_release); }
