//! Small math FFI surface used to demonstrate cbindgen header
//! generation. The struct shows that cbindgen handles user types,
//! not just primitives.

/// Summary statistics over a slice of f64.
#[repr(C)]
pub struct Stats {
    pub min: f64,
    pub max: f64,
    pub mean: f64,
}

/// Compute Stats over the slice `[ptr, ptr+len)`. Returns zeros for
/// an empty input.
///
/// # Safety
/// `ptr` must point to `len` valid `f64` values.
#[no_mangle]
pub unsafe extern "C" fn rust_math_stats(ptr: *const f64, len: usize) -> Stats {
    if ptr.is_null() || len == 0 {
        return Stats { min: 0.0, max: 0.0, mean: 0.0 };
    }
    let slice = std::slice::from_raw_parts(ptr, len);
    let mut min = slice[0];
    let mut max = slice[0];
    let mut sum = 0.0;
    for &v in slice {
        if v < min { min = v; }
        if v > max { max = v; }
        sum += v;
    }
    Stats { min, max, mean: sum / (len as f64) }
}

/// Clamp `x` to `[lo, hi]`.
#[no_mangle]
pub extern "C" fn rust_math_clamp(x: f64, lo: f64, hi: f64) -> f64 {
    x.max(lo).min(hi)
}
