! SPDX-License-Identifier: MIT
! Fortran main program calling a C++ function via BIND(C) interface.
PROGRAM main
  IMPLICIT NONE

  INTERFACE
    SUBROUTINE greet_from_cxx(name) BIND(C, NAME='greet_from_cxx')
      USE iso_c_binding, ONLY: c_char
      CHARACTER(KIND=c_char), INTENT(IN) :: name(*)
    END SUBROUTINE
  END INTERFACE

  CALL greet_from_cxx("Fortran" // CHAR(0))
END PROGRAM main
