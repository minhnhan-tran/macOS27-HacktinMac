// Ordinary generic Swift protocol dispatch; the C harness supplies explicit
// metadata and witness arguments using the emitted swiftcc ABI.
@_silgen_name("m27_equatable_compare")
@inline(never)
public func compare<T: Equatable>(_ lhs: T, _ rhs: T) -> Bool {
    lhs == rhs
}

@_silgen_name("m27_hashable_compare")
@inline(never)
public func compareHashable<T: Hashable>(_ lhs: T, _ rhs: T) -> Bool {
    lhs == rhs
}

@_silgen_name("m27_hashable_hash")
@inline(never)
public func hash<T: Hashable>(_ value: T) -> Int {
    var hasher = Hasher()
    value.hash(into: &hasher)
    return hasher.finalize()
}

@_silgen_name("check_witness_values")
func checkValues(_ left: UnsafeRawPointer, _ right: UnsafeRawPointer,
                 _ type: UnsafeRawPointer, _ expected: Bool) -> Bool

@_cdecl("run_witness_values")
public func runValues(_ expectedType: UnsafeRawPointer) -> Int32 {
    let type = unsafeBitCast(Optional<UInt32>.self, to: UnsafeRawPointer.self)
    guard type == expectedType else { return -1 }
    let values: [UInt32?] = [nil, 0, 1, 0x11223344, UInt32.max]
    var count: Int32 = 0
    for var left in values {
        for var right in values {
            let expected = left == right
            let result = withUnsafePointer(to: &left) { l in
                withUnsafePointer(to: &right) { r in
                    checkValues(l, r, type, expected)
                }
            }
            guard result else { return -2 }
            count += 1
        }
    }
    return count
}
