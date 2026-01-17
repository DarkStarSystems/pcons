/* Physics library header */
#ifndef PHYSICS_LIB_H
#define PHYSICS_LIB_H

/* Calculate kinetic energy: 0.5 * mass * velocity^2 (simplified to integer) */
int physics_kinetic_energy(int mass, int velocity);

/* Calculate momentum: mass * velocity */
int physics_momentum(int mass, int velocity);

#endif /* PHYSICS_LIB_H */
