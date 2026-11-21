extern int m27_legacy_numbers[];
extern int m27_legacy_transform(int input);
__attribute__((weak)) int m27_legacy_identity = 41;
static int local = 5;
static int *volatile local_pointer = &local;
int m27_legacy_via_b(int input) {
    return m27_legacy_transform(input) + m27_legacy_numbers[1] + *local_pointer;
}
int *m27_legacy_identity_b(void) { return &m27_legacy_identity; }
