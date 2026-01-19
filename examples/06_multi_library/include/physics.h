/* SPDX-License-Identifier: MIT */
#ifndef PHYSICS_H
#define PHYSICS_H

typedef struct {
    double x, y, z;
    double vx, vy, vz;
    double mass;
} Body;

void body_update(Body* body, double dt);
double body_kinetic_energy(const Body* body);

#endif
