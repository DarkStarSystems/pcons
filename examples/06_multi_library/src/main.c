/* SPDX-License-Identifier: MIT */
#include <stdio.h>
#include "physics.h"
#include "math_utils.h"

int main(void) {
    Body planet = {
        .x = 0, .y = 0, .z = 0,
        .vx = 1.0, .vy = 2.0, .vz = 0.5,
        .mass = 100.0
    };

    printf("Initial position: (%.2f, %.2f, %.2f)\n", planet.x, planet.y, planet.z);
    printf("Velocity magnitude: %.2f\n", vec_length(planet.vx, planet.vy, planet.vz));
    printf("Kinetic energy: %.2f\n", body_kinetic_energy(&planet));

    /* Simulate 10 time steps */
    for (int i = 0; i < 10; i++) {
        body_update(&planet, 0.1);
    }

    printf("Final position: (%.2f, %.2f, %.2f)\n", planet.x, planet.y, planet.z);
    return 0;
}
