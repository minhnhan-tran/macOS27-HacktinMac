/* Unit test of m27/logd.h: which executable is the unified log's server, where m27 keeps off
 * libsystem_trace (the PC boot stall of 2026-11-26: logd waiting for itself in m27_diag_init). */
#include <stdio.h>
#include "m27/logd.h"

int main(void)
{
    static const struct { const char *path; bool logd; } c[] = {
        {"/usr/libexec/logd", true},          {"/Volumes/Root/usr/libexec/logd", true},
        {"/usr/libexec/logd_helper", false},  {"/usr/libexec/xlogd", false},
        {"/usr/libexec/log", false},          {"/usr/sbin/notifyd", false},
        {"logd", false},                      {"", false},
        {NULL, false},
    };
    int bad = 0;
    for (size_t i = 0; i < sizeof c / sizeof *c; i++)
        if (m27_logd_path(c[i].path) != c[i].logd) {
            printf("FAIL m27_logd_path(%s) != %d\n", c[i].path ? c[i].path : "NULL", c[i].logd);
            bad++;
        }
    if (!bad) puts("logd_test: ok");
    return bad != 0;
}
