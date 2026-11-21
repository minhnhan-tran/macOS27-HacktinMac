int m27_legacy_numbers[] = {7, 13, 29, 31};
__attribute__((weak)) int m27_legacy_identity = 23;
int m27_legacy_transform(int input) { return input * 3 + m27_legacy_numbers[0]; }
int *m27_legacy_identity_a(void) { return &m27_legacy_identity; }
