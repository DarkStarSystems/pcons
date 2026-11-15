// SPDX-License-Identifier: MIT
#include <cuda_runtime.h>

__global__ void add_one(int* x) { *x += 1; }

// v + 1, computed on the GPU, or -1 (with the CUDA error in *error) when
// there is none.
extern "C" int gpu_add_one(int v, const char** error) {
    int* d = nullptr;
    cudaError_t status = cudaMalloc(&d, sizeof(int));
    if (status != cudaSuccess) {
        *error = cudaGetErrorString(status);
        return -1;
    }
    cudaMemcpy(d, &v, sizeof(int), cudaMemcpyHostToDevice);
    add_one<<<1, 1>>>(d);
    cudaMemcpy(&v, d, sizeof(int), cudaMemcpyDeviceToHost);
    cudaFree(d);
    return v;
}
