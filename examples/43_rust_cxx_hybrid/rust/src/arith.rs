//! Arithmetic kept in a module of its own, so the crate spans more than
//! one file and cargo's dep-info lists them all.

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
