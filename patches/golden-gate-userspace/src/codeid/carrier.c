/*
 * The code identity carrier's hooks (README "Code identity"; src/codeid_carrier.py links them into the x86_64
 * shared cache beside macos27tool-runtime's libm27_codeid.a).
 *
 * SecCodeCheckValidityWithErrors and SecStaticCodeCheckValidityWithErrors begin with a jump here. Each hook runs
 * Apple's body unchanged (m27_carrier_original_*: the body's relocated prologue, then a jump back into it) and hands
 * its status to m27_codeid_decide, which changes only a requirement failure of code the lists in /usr/lib/m27 name.
 * SecCodeCheckValidity and SecStaticCodeCheckValidity fall into these bodies with errors == NULL.
 *
 * Linked without a symbol table of its own: no absolute pointers, no unwind tables needed (no C++ frames).
 */
#include <m27/codeid.h>

OSStatus m27_carrier_original_SecCodeCheckValidityWithErrors(SecCodeRef, SecCSFlags, SecRequirementRef, CFErrorRef *);
OSStatus m27_carrier_original_SecStaticCodeCheckValidityWithErrors(SecStaticCodeRef, SecCSFlags, SecRequirementRef,
                                                                   CFErrorRef *);
OSStatus m27_carrier_SecCodeCheckValidityWithErrors(SecCodeRef, SecCSFlags, SecRequirementRef, CFErrorRef *);
OSStatus m27_carrier_SecStaticCodeCheckValidityWithErrors(SecStaticCodeRef, SecCSFlags, SecRequirementRef,
                                                          CFErrorRef *);

OSStatus m27_carrier_SecCodeCheckValidityWithErrors(SecCodeRef code, SecCSFlags flags, SecRequirementRef requirement,
                                                    CFErrorRef *errors)
{
    OSStatus status = m27_carrier_original_SecCodeCheckValidityWithErrors(code, flags, requirement, errors);
    return m27_codeid_decide((SecStaticCodeRef)code, requirement, status, errors);
}

OSStatus m27_carrier_SecStaticCodeCheckValidityWithErrors(SecStaticCodeRef code, SecCSFlags flags,
                                                          SecRequirementRef requirement, CFErrorRef *errors)
{
    OSStatus status = m27_carrier_original_SecStaticCodeCheckValidityWithErrors(code, flags, requirement, errors);
    return m27_codeid_decide(code, requirement, status, errors);
}
