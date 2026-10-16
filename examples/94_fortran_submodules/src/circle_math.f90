! Implementation of area(), in a submodule of circle.
SUBMODULE (circle) circle_math
  IMPLICIT NONE
  REAL, PARAMETER :: pi = 3.14159265
CONTAINS
  MODULE PURE FUNCTION area(r) RESULT(a)
    REAL, INTENT(IN) :: r
    REAL :: a
    a = pi * r * r
  END FUNCTION area
END SUBMODULE circle_math
