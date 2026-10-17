! The capital .F90 runs the preprocessor, which finds message.h in this
! file's own directory.
program main
  use greetings, only: greet
  implicit none
#include "message.h"
  call greet(MESSAGE)
end program main
