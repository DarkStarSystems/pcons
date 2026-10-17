! Implementation of area(), in a submodule of circle, whose pi it uses.
SUBMODULE (circle) circle_math
  IMPLICIT NONE
CONTAINS
  MODULE PURE FUNCTION area(r) RESULT(a)
    REAL, INTENT(IN) :: r
    REAL :: a
    a = pi * r * r
  END FUNCTION area
END SUBMODULE circle_math
