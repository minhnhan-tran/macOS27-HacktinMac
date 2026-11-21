/* m27: command-line / kext launcher front end (see src/main.c). */
int m27_cli_main(int argc, char **argv, char **envp);
/* Marks the CLI launcher for libm27_identity (src/process_identity.c): only
 * in this main executable does it take the guest path from M27_EXEC. An
 * embedded program's stub never does, so its environment cannot make it
 * load another program under the stub's code identity. */
__attribute__((used, section("__TEXT,__m27_cli"))) static const char m27_cli_marker = 1;
int main(int argc, char **argv, char **envp) { return m27_cli_main(argc, argv, envp); }

/* The format libm27_identity sends a guest's composed os_log text with (src/oslog.c). It must be in the
 * main executable's __cstring: logd resolves a format from the uuidtext of the image the record names. */
__attribute__((used, visibility("default"))) const char *const m27_oslog_format = "%{public}s";
