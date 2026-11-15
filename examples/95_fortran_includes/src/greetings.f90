! INCLUDE is part of Fortran, so this source needs no preprocessor. The
! compiler finds greeting.inc on the include path the build script sets.
module greetings
  implicit none
  include 'greeting.inc'
contains
  subroutine greet(text)
    character(*), intent(in) :: text
    write (*, '(a)') salutation // ' ' // text
  end subroutine greet
end module greetings
