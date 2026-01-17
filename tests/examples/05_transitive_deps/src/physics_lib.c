/* Physics library implementation - uses math library */
#include "physics_lib.h"
#include "math_lib.h"

int physics_kinetic_energy(int mass, int velocity) {
    /* KE = 0.5 * m * v^2, simplified for integers */
    int v_squared = math_multiply(velocity, velocity);
    return math_multiply(mass, v_squared) / 2;
}

int physics_momentum(int mass, int velocity) {
    /* p = m * v */
    return math_multiply(mass, velocity);
}
