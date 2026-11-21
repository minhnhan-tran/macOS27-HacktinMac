/* The provenance of m27's generated tables (src/NAME_gen.inc).
 *
 * Each generated table names its inputs in its own header and in
 * generated.lock (sha256, export id, format and, by input kind, the OS build,
 * the SDK build and the shared cache UUID). Every table derived from a shared
 * cache (input kind "cache") was derived from M27_GEN_OS_BUILD's arm64e and
 * x86_64 caches; tables from the SDK headers name their SDK build, and those
 * also checked against an import census name the census's OS build. The
 * generators are not part of this repository; their output is committed here
 * byte for byte, and no row is edited by hand. Each table checks
 * M27_GEN_FORMAT where it is included, so a table written for another layout
 * does not compile against this runtime; the includer includes this header
 * first.
 *
 * At startup (m27_bridge_init) m27 compares M27_GEN_OS_BUILD with the
 * running system's kern.osversion and, under -v, says when they differ. A
 * different build is expected to work: the tables describe interfaces that
 * rarely change between builds. Where one did, a table can be stale (a
 * function that gained an aggregate parameter, a new variadic export, ...),
 * and the -v line says which build the tables describe. */
#ifndef M27_GEN_STAMP_H
#define M27_GEN_STAMP_H

#define M27_GEN_OS_VERSION "27.2.1" /* the macOS release the cache tables describe */
#define M27_GEN_OS_BUILD "26B131"   /* its build (kern.osversion) */
#define M27_GEN_FORMAT 1            /* the table layout the includers read */

/* Under -v: one line when the running system's build is not M27_GEN_OS_BUILD.
 * Writes the running build to `running` (n bytes; "" when unknown); returns
 * whether it matches. */
#include <stdbool.h>
#include <stddef.h>
bool m27_gen_stamp_check(char *running, size_t n);

#endif
