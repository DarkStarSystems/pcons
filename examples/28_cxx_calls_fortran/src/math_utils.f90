! SPDX-License-Identifier: MIT
! Fortran subroutine callable from C++ via BIND(C).
SUBROUTINE fortran_sum(a, b, result) BIND(C, NAME='fortran_sum')
  USE iso_c_binding, ONLY: c_double
  IMPLICIT NONE
  REAL(c_double), INTENT(IN), VALUE :: a, b
  REAL(c_double), INTENT(OUT) :: result
  result = a + b
END SUBROUTINE
