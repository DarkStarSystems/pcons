/* Main program demonstrating transitive dependencies.
 *
 * This file only includes physics_lib.h directly, but it can also
 * use math_lib.h because physics_lib's public includes are propagated
 * transitively through the build system.
 */
#include <stdio.h>
#include "physics_lib.h"
#include "math_lib.h"  /* Available transitively! */

int main(void) {
    int mass = 10;
    int velocity = 5;

    /* Use physics library */
    int ke = physics_kinetic_energy(mass, velocity);
    int momentum = physics_momentum(mass, velocity);

    /* Use math library directly (transitive dependency) */
    int sum = math_add(ke, momentum);

    printf("Mass: %d kg, Velocity: %d m/s\n", mass, velocity);
    printf("Kinetic Energy: %d J\n", ke);
    printf("Momentum: %d kg*m/s\n", momentum);
    printf("Sum (KE + p): %d\n", sum);

    return 0;
}
