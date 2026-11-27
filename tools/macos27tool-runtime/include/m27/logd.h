#ifndef M27_LOGD_H
#define M27_LOGD_H
#include <stdbool.h>
#include <string.h>

/* logd is the unified log's server. libsystem_trace in its process must be told so
 * (os_log_set_client_type, the first thing logd's main does) before anything uses it; otherwise its first
 * use asks logd for the logging preferences and waits for logd itself, forever. In logd m27 keeps off
 * libsystem_trace (m27_native_log_off) and applies the guest's os_log_set_client_type (src/interpose.c). */
static inline bool m27_logd_path(const char *p)
{
    static const char tail[] = "/usr/libexec/logd";   /* also under a staged root */
    size_t n = p ? strlen(p) : 0, t = sizeof tail - 1;
    return n >= t && !strcmp(p + n - t, tail);
}

/* True in logd's process: m27 must not call libsystem_trace there (src/runtime.c). */
bool m27_native_log_off(void);

#endif
