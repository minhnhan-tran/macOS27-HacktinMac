# m27 — arm64e -> x86_64 translator ("reverse Rosetta").
# Host artifacts are x86_64; oracle tools are native arm64.
# MACOS_MIN is the deployment target of every x86_64 artifact (m27, libm27,
# m27-stub, front/stub.o; tools/m27-embed relinks stubs with the minos it reads
# from front/stub.o). Build another target into its own directory, e.g.
#   make B=build/macos-26.0                    (macOS 26 Tahoe userspace)
# A build directory named macos-VER implies MACOS_MIN=VER (default 27.0), and
# a MACOS_MIN that contradicts that name is an error.
CC        := clang
B         := build
B_MIN     := $(patsubst macos-%,%,$(filter macos-%,$(notdir $(B:/=))))
MACOS_MIN := $(or $(B_MIN),27.0)
ifneq ($(B_MIN),)
ifneq ($(B_MIN),$(MACOS_MIN))
$(error B=$(B) is the macOS $(B_MIN) build directory, not MACOS_MIN=$(MACOS_MIN))
endif
endif
# The CPU profile m27-x86_64-avx-cx16-v1 (docs/releases.md): Intel Macs with AVX, so also SSSE3, SSE4.1,
# SSE4.2, POPCNT and CMPXCHG16B. Compiled code and the translator use at most x86-64-v2 (no VEX encodings:
# Rosetta, where the tests run, has no AVX), except vzeroupper where CPUID and XCR0 report AVX state
# (src/jit.c m27_vzeroupper); src/main.c refuses a CPU without the profile at startup.
ARCH      := -arch x86_64 -mmacosx-version-min=$(MACOS_MIN) -march=x86-64-v2
# Debug info names sources and objects relative to the checkout, and the debug
# map and libm27core.a carry no file times (ZERO_AR_DATE), so the same commit
# gives the same bytes in any checkout, worktree or rebuild.
export ZERO_AR_DATE := 1
REPRO     := -ffile-prefix-map=$(CURDIR)=.
# TEST_SWITCHES=1: a build that reads the test-only switches (M27_LIBVAL_USER,
# M27_SIGRETURN_USER, ...; src/aot_policy.c). Never the build that ships.
ifdef TEST_SWITCHES
REPRO     += -DM27_TEST_SWITCHES
endif
# SURVEY=1: a diagnostics build for a survey image, which honours
# m27_swift_value_audit=1 (src/opts.c BOOT_SURVEY). Never the build that ships.
ifdef SURVEY
REPRO     += -DM27_SURVEY_BUILD
endif
WARN      := -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers
CFLAGS    := $(ARCH) -std=gnu11 -O2 -g -ffp-contract=off $(WARN) -Iinclude -Isrc -mcx16 $(REPRO)
OSO       := -Wl,-oso_prefix,$(CURDIR)/
# Changing MACOS_MIN in one B rebuilds everything there.
MIN_STAMP := $(B)/.macos-min-$(MACOS_MIN)

CORE := src/a64_interp.c src/a64_simd.c src/a64_fp.c src/sysreg.c src/x86_emit.c \
        src/a64_imm.c src/a64_cond.c src/a64_regs.c src/aot_policy.c src/opts.c src/ifunit.c src/gmap.c src/x86args.c src/cpufeat.c \
        src/native_callback.c
CORE_O := $(patsubst src/%.c,$(B)/%.o,$(CORE))

RT := src/bridge.c src/macho.c src/code.c src/libval.c src/dsc.c src/commpage.c src/runtime.c src/interpose.c src/varsniff.c src/vafmt.c src/syscall.c src/signals.c src/objc.c src/regvar.c src/abi.c src/blocks.c src/fnslots.c src/swiftmeta.c src/swiftobjects.c src/callbacks.c src/gss.c src/cxxstr.c src/thunks.c src/libm.c src/swift.c src/swiftcc.c src/jit.c src/trust.c src/idlist.c src/guest_sig.c src/main.c src/unwind.c
RT += src/swiftresult.c src/swiftvalue.c src/swiftschema.c
RT += src/async_contract.c src/valist_builder.c
RT += src/ioreg.c src/ids.c src/aot_use.c
RT += src/swiftvalue/Recipe.c src/swiftvalue/Enum.c src/swiftvalue/Common.c src/swiftvalue/Aggregate.c
RT += src/swiftvalue/NativeLeaf.c src/swiftvalue/Tables.c src/swiftvalue/Facts.c
RT_O := $(patsubst src/%.c,$(B)/%.o,$(RT)) $(B)/bridge_x86_64.o

# libm27.dylib holds the runtime; m27 (CLI, kext launcher) and m27-stub (the
# x86_64 slice tools/m27-embed adds to arm64e-only programs) are front ends.
M27_LIBDIR ?= /usr/lib/m27
FRONT_LD := -Wl,-rpath,@executable_path -Wl,-rpath,$(M27_LIBDIR)
# Front ends get CFLAGS' warnings but no -g: they are linked straight from
# source (a debug map would name temporary objects), and front/stub.o is
# linked into every embedded program by tools/m27-embed.
FRONT_CFLAGS := $(ARCH) -O2 $(WARN)

.PHONY: image-scan all test check test-guest oracle unit aot forktest jitlocktest bridgetest async-contract-test fuzz fuzz-smoke catalog-gate
all: $(B)/libm27core.a $(B)/libm27.dylib $(B)/m27 $(B)/m27-stub $(B)/front/stub.o $(B)/libm27_codeid.a

$(MIN_STAMP):
	@mkdir -p $(B)
	@rm -f $(B)/.macos-min-*
	@touch $@

$(B)/bridge_x86_64.o: src/bridge_x86_64.S $(MIN_STAMP)
	$(CC) $(CFLAGS) -c $< -o $@

# Linked by m27 and m27-stub (not inserted): its __interpose tuples apply
# process-wide and the main executable stays dyld image 0 (src/process_identity.c).
# src/oslog.c: guest os_log/os_signpost records composed in process (it says why).
$(B)/libm27_identity.dylib: src/process_identity.c src/oslog.c src/native_bundle_identity.c include/m27/native_bundle.h include/m27/identity.h $(MIN_STAMP)
	$(CC) $(FRONT_CFLAGS) -Iinclude -dynamiclib -install_name @rpath/libm27_identity.dylib -o $@ src/process_identity.c src/oslog.c src/native_bundle_identity.c -lobjc
	codesign -s - -f $@

# m27 code runs in signal handlers on native threads that may hold the malloc
# locks, and dyld allocates a thread's __thread block on its first access:
# libm27 must have no thread-local variables (pthread keys instead).
NO_TLV = @if otool -l $@ | grep -q __thread_vars; then \
	echo "$@: has thread-local variables (__thread_vars); use a pthread key (src/macho.c, last_code_img)" >&2; \
	rm -f $@; exit 1; fi

$(B)/libm27.dylib: $(RT_O) $(B)/libm27core.a
	$(CC) $(ARCH) -dynamiclib -install_name @rpath/libm27.dylib $(OSO) -o $@ $(RT_O) $(B)/libm27core.a -lobjc
	$(NO_TLV)
	codesign -s - -f $@

$(B)/m27: src/front/cli.c $(B)/libm27.dylib $(B)/libm27_identity.dylib
	$(CC) $(FRONT_CFLAGS) -o $@ $< -L$(B) -lm27 -lm27_identity $(FRONT_LD)
	codesign -s - -f --entitlements tools/m27.entitlements $@

# Object form for per-program stubs that carry the program's own data sections.
$(B)/front/stub.o: src/front/stub.c $(MIN_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(FRONT_CFLAGS) -c $< -o $@

# The code identity Security.framework gives listed stubs and re-signed native programs
# (include/m27/codeid.h): no libm27, for the image's x86_64 Security.framework to link.
CODEID_O := $(B)/codeid/code_identity.o $(B)/codeid/idlist.o
$(B)/codeid/%.o: src/%.c include/m27/codeid.h include/m27/idlist.h $(MIN_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(FRONT_CFLAGS) $(REPRO) -c $< -o $@
$(B)/libm27_codeid.a: $(CODEID_O)
	rm -f $@ && libtool -static -o $@ $(CODEID_O)

# Unsigned: tools/m27-embed signs each copy with its program's identity.
$(B)/m27-stub: src/front/stub.c $(B)/libm27.dylib $(B)/libm27_identity.dylib $(MIN_STAMP)
	$(CC) $(FRONT_CFLAGS) -o $@ $< -L$(B) -lm27 -lm27_identity -Wl,-rpath,$(M27_LIBDIR) -Wl,-no_adhoc_codesign

$(B)/%.o: src/%.c $(wildcard include/m27/*.h src/*.inc) $(MIN_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

# A native callee that throws unwinds through m27_cxxstr_call: its cleanup destroys the native strings.
$(B)/cxxstr.o: src/cxxstr.c $(wildcard include/m27/*.h src/*.inc) $(MIN_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -fexceptions -c $< -o $@

$(B)/libm27core.a: $(CORE_O)
	ar rcs $@ $^

$(B)/oracle-native: tests/oracle/native_arm64.c tests/oracle/record.h tests/oracle/fma_cases.h tests/oracle/known.h
	@mkdir -p $(B)
	$(CC) -arch arm64 -O1 -Wall -o $@ tests/oracle/native_arm64.c && codesign -s - -f $@

$(B)/oracle-check: tests/oracle/check.c tests/oracle/record.h tests/oracle/known.h $(B)/libm27core.a $(B)/jit.o $(B)/stubs_oracle.o
	$(CC) $(CFLAGS) -o $@ $< $(B)/jit.o $(B)/stubs_oracle.o $(B)/libm27core.a && codesign -s - -f $@

# Concurrency and memory-permission checks the record format cannot express:
# 16-byte LDP/STP/LDXP atomicity across threads, exclusives on read-only memory.
$(B)/oracle-atomic: tests/oracle/atomic16.c $(B)/libm27core.a $(B)/jit.o $(B)/stubs_oracle.o
	$(CC) $(CFLAGS) -o $@ $< $(B)/jit.o $(B)/stubs_oracle.o $(B)/libm27core.a -lpthread && codesign -s - -f $@

# Every mode runs; the target fails if any of them reports a failure. The
# simd records (AES, PMULL) are checked again with the JIT translating for no
# optional CPU feature (m27/cpufeat.h): the helper paths those lowerings skip.
ORACLE_N ?= 200000
ORACLE_MODES ?= dp simd mem block fma known reserved fmad
oracle: $(B)/oracle-native $(B)/oracle-check $(B)/oracle-atomic
	@fail=0; for m in $(ORACLE_MODES); do \
	  timeout -s KILL 900 $(B)/oracle-native $(B)/oracle-$$m.bin $(ORACLE_N) 0x$$(date +%s)$$RANDOM $$m || fail=1; \
	  echo "== $$m"; timeout -s KILL 900 $(B)/oracle-check $(B)/oracle-$$m.bin --show 5 --bad $(B)/oracle_bad_$$m.txt || fail=1; \
	  if [ $$m = simd ]; then echo "== $$m (JIT without optional CPU features)"; \
	    timeout -s KILL 900 $(B)/oracle-check $(B)/oracle-$$m.bin --show 5 --bad $(B)/oracle_bad_$$m-cpu0.txt --cpu 0 || fail=1; fi; \
	done; \
	echo "== atomic"; timeout -s KILL 300 $(B)/oracle-atomic || fail=1; \
	exit $$fail

# The same oracle checks with the translator itself (interpreter, JIT, x86
# encoder: CORE, src/jit.c) built under ASan and UBSan, objects in $(B)/asan.
# Any sanitizer report fails the target; reports are left in
# $(B)/asan/oracle-*.log. ORACLE_ASAN_N records per mode (default 20000).
ASAN_CFLAGS := $(filter-out -O2,$(CFLAGS)) -O1 -fsanitize=address,undefined -fno-omit-frame-pointer
ASAN_CORE_O := $(patsubst src/%.c,$(B)/asan/%.o,$(CORE) src/jit.c src/stubs_oracle.c)
ASAN_ENV    := ASAN_OPTIONS=symbolize=0:handle_segv=0:handle_sigbus=0:handle_sigill=0:handle_sigfpe=0:detect_leaks=0 \
               UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=0
ORACLE_ASAN_N ?= 20000
$(B)/asan/%.o: src/%.c $(wildcard include/m27/*.h src/*.inc) $(MIN_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(ASAN_CFLAGS) -c $< -o $@
$(B)/asan/oracle-check: tests/oracle/check.c tests/oracle/record.h tests/oracle/known.h $(ASAN_CORE_O)
	$(CC) $(ASAN_CFLAGS) -o $@ $< $(ASAN_CORE_O) && codesign -s - -f $@
$(B)/asan/oracle-atomic: tests/oracle/atomic16.c $(ASAN_CORE_O)
	$(CC) $(ASAN_CFLAGS) -o $@ $< $(ASAN_CORE_O) -lpthread && codesign -s - -f $@
.PHONY: oracle-asan
oracle-asan: $(B)/oracle-native $(B)/asan/oracle-check $(B)/asan/oracle-atomic
	@fail=0; seed=0x$$(date +%s)$$RANDOM; echo "seed $$seed"; for m in $(ORACLE_MODES); do \
	  timeout -s KILL 900 $(B)/oracle-native $(B)/asan/oracle-$$m.bin $(ORACLE_ASAN_N) $$seed $$m || fail=1; \
	  echo "== $$m (asan)"; $(ASAN_ENV) timeout -s KILL 900 $(B)/asan/oracle-check $(B)/asan/oracle-$$m.bin --show 5 \
	    --bad $(B)/asan/oracle_bad_$$m.txt >$(B)/asan/oracle-$$m.log 2>&1 || fail=1; tail -3 $(B)/asan/oracle-$$m.log; \
	done; \
	echo "== atomic (asan)"; $(ASAN_ENV) ATOMIC16_SECONDS=10 timeout -s KILL 300 $(B)/asan/oracle-atomic >$(B)/asan/oracle-atomic.log 2>&1 || fail=1; \
	tail -3 $(B)/asan/oracle-atomic.log; \
	if grep -lE 'runtime error:|ERROR: AddressSanitizer' $(B)/asan/oracle-*.log; then echo "sanitizer reports in the logs above"; fail=1; fi; \
	exit $$fail

# Unit tests of the A64 immediate decoder, condition mapping and x8 input walk.
$(B)/test_imm_cond: tests/a64/test_imm_cond.c src/a64_imm.c src/a64_cond.c src/a64_regs.c include/m27/a64_imm.h include/m27/a64_cond.h include/m27/a64_regs.h include/m27/cpu.h include/m27/x86.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -o $@ tests/a64/test_imm_cond.c src/a64_imm.c src/a64_cond.c src/a64_regs.c && codesign -s - -f $@

# Unit test of the switch snapshot parser (src/opts.c) with the tier rules.
$(B)/opts_test: tests/aot/opts_test.c $(B)/libm27core.a
	$(CC) $(CFLAGS) -o $@ $< $(B)/libm27core.a && codesign -s - -f $@

# Fork while other threads hold the JIT, thunk, signature, bridge, signal-disposition
# and name-index locks.
$(B)/test_fork: tests/host/fork.c $(B)/libm27.dylib
	$(CC) $(ARCH) -O1 -Iinclude -o $@ $< -L$(B) -lm27 -Wl,-rpath,$(abspath $(B)) && codesign -s - -f $@

forktest: $(B)/test_fork
	timeout -s KILL 300 $(B)/test_fork 300 all

# The bridge plan table: capacity, lock-free reads while it grows, upgrades
# (tests/host/bridge.c).
$(B)/test_bridge: tests/host/bridge.c $(B)/libm27.dylib
	$(CC) $(ARCH) -O1 -Iinclude -o $@ $< -L$(B) -lm27 -Wl,-rpath,$(abspath $(B)) && codesign -s - -f $@

bridgetest: $(B)/test_bridge
	timeout -s KILL 120 $(B)/test_bridge

# Code cache and thunk areas: AOT load vs translation, signature thunks,
# cache exhaustion (tests/host/jitlock.c).
jitlocktest: $(B)/libm27.dylib $(B)/m27
	OUT=$(abspath $(B))/jitlock LIB=$(B) tests/host/jitlock.sh

# m27's growable maps (src/gmap.c): capacity, lock-free reads while they grow, fork.
$(B)/test_gmap: tests/host/gmap.c src/gmap.c include/m27/gmap.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -o $@ tests/host/gmap.c src/gmap.c -lpthread && codesign -s - -f $@

# Native callback-call contracts (src/native_callback.c): validation, binding by UUID and call site, lookups.
$(B)/test_native_callback: tests/host/native_callback.c src/native_callback.c src/gmap.c include/m27/native_callback.h include/m27/gmap.h include/m27/swift_reverse.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -o $@ tests/host/native_callback.c src/native_callback.c src/gmap.c && codesign -s - -f $@

# x86_64 instruction lengths and native stack-argument slots (src/x86args.c): the packed-layout check.
$(B)/test_x86args: tests/host/x86args.c src/x86args.c include/m27/x86args.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -o $@ tests/host/x86args.c src/x86args.c && codesign -s - -f $@

# Unit test of the type-encoding layout and classification in src/abi.c.
$(B)/test_abi_layout: tests/abi/test_layout.m src/abi.c $(wildcard include/m27/*.h) $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -Wno-unused-function -framework Foundation -o $@ tests/abi/test_layout.m && codesign -s - -f $@


# Unit test of the validating Mach-O loader (src/macho.c), under ASan, on
# corrupted copies of the fuzz seeds.
$(B)/test_macho: tests/loader/test_macho.c src/macho.c src/code.c src/libval.c include/m27/macho.h include/m27/code.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(ARCH) -std=gnu11 -O1 -g -Iinclude -fsanitize=address -fno-omit-frame-pointer $(REPRO) -o $@ tests/loader/test_macho.c src/macho.c src/code.c src/libval.c && codesign -s - -f $@

# m27_images lookups on reader threads while loads, unloads and failed loads
# change the list (ASan).
$(B)/test_images: tests/loader/test_images.c src/macho.c src/code.c src/libval.c include/m27/macho.h include/m27/code.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(ARCH) -std=gnu11 -O1 -g -Iinclude -fsanitize=address -fno-omit-frame-pointer $(REPRO) -o $@ tests/loader/test_images.c src/macho.c src/code.c src/libval.c && codesign -s - -f $@

# The carried-section reader of libm27_identity (src/process_identity.c), under
# ASan, on crafted guest files.
$(B)/test_identity: tests/loader/test_identity.c src/process_identity.c src/oslog.c src/native_bundle_identity.c include/m27/identity.h include/m27/native_bundle.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(ARCH) -std=gnu11 -O1 -g -Iinclude -fsanitize=address -fno-omit-frame-pointer $(REPRO) -o $@ tests/loader/test_identity.c src/oslog.c src/native_bundle_identity.c -lobjc && codesign -s - -f $@

# Unit test of the interface-unit parser (src/ifunit.c), under ASan/UBSan: one
# corruption per rule and every single-bit edit of the fuzz seeds.
$(B)/test_ifunit: tests/loader/test_ifunit.c src/ifunit.c src/aot_policy.c src/cpufeat.c include/m27/bridge.h include/m27/aot.h $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(ARCH) -std=gnu11 -O1 -g -Iinclude -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer $(REPRO) -o $@ tests/loader/test_ifunit.c src/ifunit.c src/aot_policy.c src/cpufeat.c && codesign -s - -f $@

$(B)/test_helper_descriptor: tests/aot/helper_descriptor.c $(B)/jit.o $(B)/stubs_oracle.o $(B)/libm27core.a
	$(CC) $(CFLAGS) -o $@ $< $(B)/jit.o $(B)/stubs_oracle.o $(B)/libm27core.a
	codesign -s - -f $@

# Only the unit build counts descriptor helper routing, including declines
# whose result bits happen to equal the inadmissible inline attempt.
$(B)/jit-vector.o: src/jit.c $(wildcard src/*.inc include/m27/*.h) $(MIN_STAMP)
	@mkdir -p $(B)
	$(CC) $(CFLAGS) -Da64_exec_helper=jit_vector_helper -c $< -o $@

# Translated vector FP, shifts and structure loads/stores against the interpreter.
$(B)/test_jit_vector: tests/aot/jit_vector.c tests/oracle/fma_cases.h tests/oracle/fma16_cases.h $(B)/jit-vector.o $(B)/stubs_oracle.o $(B)/libm27core.a
	$(CC) $(CFLAGS) -o $@ $< $(B)/jit-vector.o $(B)/stubs_oracle.o $(B)/libm27core.a
	codesign -s - -f $@

$(B)/test_round16: tests/aot/round16.c $(B)/libm27core.a
	$(CC) $(CFLAGS) -o $@ $< $(B)/libm27core.a && codesign -s - -f $@

$(B)/test_ioreg: tests/host/ioreg.c src/ioreg.c $(MIN_STAMP)
	$(CC) $(CFLAGS) -framework CoreFoundation -o $@ $< && codesign -s - -f $@

unit: $(B)/test_round16 $(B)/test_helper_descriptor $(B)/test_jit_vector $(B)/test_imm_cond $(B)/opts_test $(B)/test_abi_layout $(B)/test_macho $(B)/test_images $(B)/test_identity $(B)/test_ifunit $(B)/test_gmap $(B)/test_x86args $(B)/test_ioreg $(B)/test_native_callback
	timeout -s KILL 30 $(B)/test_ioreg
	timeout -s KILL 120 $(B)/test_round16
	timeout -s KILL 90 $(B)/test_helper_descriptor
	timeout -s KILL 240 $(B)/test_jit_vector
	$(B)/test_imm_cond
	timeout -s KILL 120 $(B)/test_gmap
	timeout -s KILL 30 $(B)/test_native_callback
	timeout -s KILL 60 $(B)/test_x86args
	$(B)/test_abi_layout
	$(B)/opts_test
	@mkdir -p $(B)/loader-test
	$(B)/test_macho tests/fuzz/corpus/macho $(B)/loader-test
	$(B)/test_images tests/fuzz/corpus/macho $(B)/loader-test
	$(B)/test_identity $(B)/loader-test
	$(B)/test_ifunit tests/fuzz/corpus/ifunit

# AOT cache trust and format checks, plus the policy unit test
# (tests/aot/run.sh; scratch in AOT_OUT, which it empties first).
AOT_OUT ?= $(B)/aot-test
aot: all
	OUT=$(abspath $(AOT_OUT)) M27=$(abspath $(B))/m27 B=$(abspath $(B)) tests/aot/run.sh

# The checks an x86_64 shared cache with prespecialized Swift metadata must pass before m27 runs on it
# (docs/swift-metadata.md): make catalog-gate CACHE=DIR [BASE_CACHE=DIR [ACCEPT_EDITS=RECIPE.json]] [UNITS=DIR]
catalog-gate:
	@test -n "$(CACHE)" || { echo 'catalog-gate: CACHE=dyld directory or dyld_shared_cache_x86_64 needed' >&2; exit 2; }
	python3.14 -I -B tools/m27_catalog_gate.py "$(CACHE)" $(if $(BASE_CACHE),--base-cache "$(BASE_CACHE)") $(if $(UNITS),--units "$(UNITS)") $(if $(ACCEPT_EDITS),--accept-edits "$(ACCEPT_EDITS)")

async-contract-test: all
	python3.14 tests/host/async_package.py --build $(B)

$(B)/test_ioreg_guest: tests/guest/ioreg.c $(MIN_STAMP)
	$(CC) -arch arm64 -O2 -Wall -Wextra -framework IOKit -framework CoreFoundation -o $@ $<
	codesign -s - -f $@

.PHONY: ioregtest
ioregtest: all $(B)/test_ioreg_guest
	timeout -s KILL 30 $(B)/test_ioreg_guest
	timeout -s KILL 30 $(B)/m27 $(B)/test_ioreg_guest
	timeout -s KILL 30 $(B)/m27 --interp $(B)/test_ioreg_guest

test: jit-arena-test wx-cow-test unit oracle aot bridgetest async-contract-test legacy-dyld-test indexed-mulhigh-test forktest jitlocktest ioregtest
	tools/check_x86.sh
	python3.14 -I -B tests/host/fork_locks.py

# Every other test script that runs unattended on a development host
# (tests/check.list, tests/README.md), in parallel (CHECK_J jobs), each with a
# timeout and a private output directory; fails on any failure or on a skip
# tests/expected-skips.txt does not list. `make check` is the full gate.
CHECK_J ?= 8
test-guest: all $(FUZZERS)
	B=$(abspath $(B)) J=$(CHECK_J) tests/check.sh
# The guest scripts exercise test-only switches, so the gate runs them on a
# TEST_SWITCHES build in its own directory; the default build stays as shipped.
# oracle-asan (the oracle checks under ASan and UBSan) is part of the gate and
# not of `make test`: it builds the translator a second time.
check: test
	$(MAKE) oracle-asan
	$(MAKE) B=$(B)-ts TEST_SWITCHES=1 test-guest

# Fuzzing (tests/fuzz/README.md): ASan + trace-pc-guard targets driven by
# tests/fuzz/driver.c (the toolchain has no libFuzzer runtime). `make fuzz`
# builds them; tests/fuzz/run.sh runs them; `make fuzz-smoke` replays the seed
# corpus and runs each target briefly (FUZZ_TIME seconds each, default 20).
FUZZ_CFLAGS := $(ARCH) -std=gnu11 -O1 -g -Iinclude -Isrc -fsanitize=address -fno-omit-frame-pointer $(REPRO)
FUZZ_COV    := -fsanitize-coverage=trace-pc-guard
FUZZERS     := $(B)/fuzz/fuzz_macho $(B)/fuzz/fuzz_typeenc $(B)/fuzz/fuzz_codesig $(B)/fuzz/fuzz_ifunit
FUZZ_TIME   ?= 20
$(B)/fuzz/driver.o: tests/fuzz/driver.c $(MIN_STAMP)
	@mkdir -p $(dir $@)
	$(CC) $(FUZZ_CFLAGS) -c $< -o $@
$(B)/fuzz/fuzz_macho: tests/fuzz/fuzz_macho.c src/macho.c src/code.c src/libval.c include/m27/macho.h include/m27/code.h $(B)/fuzz/driver.o
	$(CC) $(FUZZ_CFLAGS) $(FUZZ_COV) -o $@ tests/fuzz/fuzz_macho.c src/macho.c src/code.c src/libval.c $(B)/fuzz/driver.o && codesign -s - -f $@
$(B)/fuzz/fuzz_typeenc: tests/fuzz/fuzz_typeenc.c src/abi.c $(wildcard include/m27/*.h) $(B)/fuzz/driver.o
	$(CC) $(FUZZ_CFLAGS) $(FUZZ_COV) -fsanitize=undefined -fno-sanitize-recover=all -Wno-unused-function -o $@ tests/fuzz/fuzz_typeenc.c $(B)/fuzz/driver.o && codesign -s - -f $@
$(B)/fuzz/fuzz_codesig: tests/fuzz/fuzz_codesig.c src/libval.c $(wildcard include/m27/*.h) $(B)/fuzz/driver.o
	$(CC) $(FUZZ_CFLAGS) $(FUZZ_COV) -fsanitize=undefined -fno-sanitize-recover=all -Wno-unused-function -o $@ tests/fuzz/fuzz_codesig.c $(B)/fuzz/driver.o && codesign -s - -f $@
$(B)/fuzz/fuzz_ifunit: tests/fuzz/fuzz_ifunit.c src/ifunit.c src/aot_policy.c src/cpufeat.c include/m27/bridge.h include/m27/aot.h $(B)/fuzz/driver.o
	$(CC) $(FUZZ_CFLAGS) $(FUZZ_COV) -fsanitize=undefined -fno-sanitize-recover=all -o $@ tests/fuzz/fuzz_ifunit.c src/ifunit.c src/aot_policy.c src/cpufeat.c $(B)/fuzz/driver.o && codesign -s - -f $@
fuzz: $(FUZZERS)
fuzz-smoke: $(FUZZERS)
	B=$(B) FUZZ_TIME=$(FUZZ_TIME) tests/fuzz/run.sh

# Host testing: a stub that finds libm27 in build/ (not for images).
$(B)/m27-stub-dev: src/front/stub.c $(B)/libm27.dylib $(MIN_STAMP)
	$(CC) $(FRONT_CFLAGS) -o $@ $< -L$(B) -lm27 -Wl,-rpath,$(abspath $(B)) -Wl,-no_adhoc_codesign

# Swift callback islands share the bounded VM-hole allocator.
$(B)/swiftmeta.o: src/near_map.h

$(B)/swiftvalue/%.o: CFLAGS += -DM27_SWIFT_VALUE_RUNTIME

# Optional local extensions: a makefile fragment named by M27_EXTRA_MK (unset by default).
ifdef M27_EXTRA_MK
include $(M27_EXTRA_MK)
endif

# Own native ARM/x86/JIT/interpreter/AOT comparisons of legacy dyld pointer streams.
.PHONY: legacy-dyld-test
legacy-dyld-test: all
	M27=$(abspath $(B))/m27 OUT=$(abspath $(B))/legacy-fixups python3.14 -I tests/guest/legacy-fixups/run.py

# Signed indexed saturating multiply-high values, aliases, upper clearing and QC.
.PHONY: indexed-mulhigh-test
indexed-mulhigh-test: all
	M27=$(abspath $(B))/m27 OUT=$(abspath $(B))/indexed-mulhigh python3.14 -I tests/guest/indexed-mulhigh/run.py

# ARM-only launchd jobs of a mounted system image: no refusal the baseline runtime lacks (needs ROOT and BASELINE)
image-scan: $(B)/m27
	OUT=$(or $(SCAN_OUT),$(B)/image-scan) tests/image/launchd-scan.sh $(ROOT) $(abspath $(B)/m27) $(BASELINE)

.PHONY: wx-cow-test
wx-cow-test: $(B)/libm27.dylib $(B)/m27 $(B)/libm27_identity.dylib
	OUT=$(abspath $(B))/wx-cow LIB=$(B) tests/host/wx-cow.sh

.PHONY: jit-arena-test
jit-arena-test: $(B)/libm27.dylib $(B)/m27
	OUT=$(abspath $(B))/jit-arena LIB=$(B) tests/host/jit-arena.sh
