import CStats  // the C library, via its module.modulemap

/// Swift API callable from C++ through the generated Analyzer-Swift.h.
public func scaledMean(_ a: Double, _ b: Double, factor: Double) -> Double {
    cstats_mean2(a, b) * factor
}
