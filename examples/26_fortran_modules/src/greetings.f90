! Fortran module that provides a greeting subroutine
MODULE greetings
  IMPLICIT NONE
CONTAINS
  SUBROUTINE say_hello()
    PRINT *, "Hello from Fortran module!"
  END SUBROUTINE say_hello
END MODULE greetings
