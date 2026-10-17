! The interface: what users of the module see. Its submodules also see
! its private entities, like pi.
MODULE circle
  IMPLICIT NONE
  PRIVATE :: pi
  REAL, PARAMETER :: pi = 3.14159265
  INTERFACE
    MODULE PURE FUNCTION area(r) RESULT(a)
      REAL, INTENT(IN) :: r
      REAL :: a
    END FUNCTION area
    MODULE FUNCTION describe(r) RESULT(text)
      REAL, INTENT(IN) :: r
      CHARACTER(LEN=40) :: text
    END FUNCTION describe
  END INTERFACE
END MODULE circle
