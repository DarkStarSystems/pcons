/* SPDX-License-Identifier: MIT */
#include "physics.h"
#include "math_utils.h"

void body_update(Body* body, double dt) {
    body->x += body->vx * dt;
    body->y += body->vy * dt;
    body->z += body->vz * dt;
}

double body_kinetic_energy(const Body* body) {
    double speed = vec_length(body->vx, body->vy, body->vz);
    return 0.5 * body->mass * speed * speed;
}
