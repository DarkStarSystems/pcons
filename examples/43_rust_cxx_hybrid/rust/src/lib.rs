//! Tiny FFI surface used by the C++ example.
//!
//! Exposes two functions:
//!   - `rust_greet_sum`: returns a + b. Used to prove the link works.
//!   - `rust_greet_message`: writes a null-terminated greeting into a
//!     caller-provided buffer; returns the number of bytes written
//!     (excluding the terminator).

use std::ffi::c_char;

/// Return the sum of two 32-bit integers. Stays simple on purpose.
#[no_mangle]
pub extern "C" fn rust_greet_sum(a: i32, b: i32) -> i32 {
    a + b
}

/// Write "Hello from Rust, <name>!" into `out` (capacity `cap` bytes,
/// including the terminator). Returns the number of bytes written
/// excluding the terminator, or -1 if the buffer is too small.
///
/// `name` must be a null-terminated UTF-8 string. Passing a null
/// pointer renders as "world".
///
/// # Safety
/// `name` must point to a valid null-terminated string or be null.
/// `out` must point to a writable buffer of at least `cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn rust_greet_message(
    name: *const c_char,
    out: *mut c_char,
    cap: usize,
) -> i64 {
    let who = if name.is_null() {
        "world".to_string()
    } else {
        match std::ffi::CStr::from_ptr(name).to_str() {
            Ok(s) => s.to_string(),
            Err(_) => return -1,
        }
    };

    let msg = format!("Hello from Rust, {who}!");
    let bytes = msg.as_bytes();

    // Need space for bytes + null terminator.
    if bytes.len() + 1 > cap {
        return -1;
    }

    std::ptr::copy_nonoverlapping(bytes.as_ptr(), out as *mut u8, bytes.len());
    *out.add(bytes.len()) = 0;
    bytes.len() as i64
}
