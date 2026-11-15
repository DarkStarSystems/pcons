! Implementation of describe(), in a submodule of circle_math.
SUBMODULE (circle:circle_math) circle_format
  IMPLICIT NONE
CONTAINS
  MODULE FUNCTION describe(r) RESULT(text)
    REAL, INTENT(IN) :: r
    CHARACTER(LEN=40) :: text
    WRITE (text, '(A,F0.2)') 'Circle area: ', area(r)
  END FUNCTION describe
END SUBMODULE circle_format
