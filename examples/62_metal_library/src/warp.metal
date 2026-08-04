// SPDX-License-Identifier: MIT
#include <metal_stdlib>
using namespace metal;

kernel void warp(device const float *in [[buffer(0)]],
                 device float *out [[buffer(1)]],
                 uint i [[thread_position_in_grid]]) {
    out[i] = in[i] * 2.0f;
}
