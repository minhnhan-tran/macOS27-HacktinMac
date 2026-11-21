/* footprint_front: m27's command-line front end (src/front/cli.c) as
 * tests/guest/footprint.sh builds it, with one difference: every guest file
 * is mapped, the way m27 takes a file that only root can change. The
 * test's budgets are about system daemons, whose files are such files; its
 * own guests are the user's files, which m27 reads into anonymous memory
 * instead (include/m27/macho.h m27_slice), and what that costs the test
 * reports on lines of its own. */
extern int m27_slice_test_path;
int m27_cli_main(int argc, char **argv, char **envp);
__attribute__((used, section("__TEXT,__m27_cli"))) static const char m27_cli_marker = 1;
int main(int argc, char **argv, char **envp)
{
    m27_slice_test_path = 1;
    return m27_cli_main(argc, argv, envp);
}

/* as src/front/cli.c: the format libm27_identity sends a guest's composed os_log text with */
__attribute__((used, visibility("default"))) const char *const m27_oslog_format = "%{public}s";
