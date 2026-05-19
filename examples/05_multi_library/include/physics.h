/* SPDX-License-Identifier: MIT */
#ifndef PHYSICS_H
#define PHYSICS_H

/* Windows DLL export/import declaration */
#ifdef _WIN32
#ifdef PHYSICS_BUILDING_DLL
#define PHYSICS_API __declspec(dllexport)
#else
#define PHYSICS_API __declspec(dllimport)
#endif
#else
#define PHYSICS_API
#endif

typedef struct {
    double x, y, z;
    double vx, vy, vz;
    double mass;
} Body;

PHYSICS_API void body_update(Body *body, double dt);
PHYSICS_API double body_kinetic_energy(const Body *body);

#endif
