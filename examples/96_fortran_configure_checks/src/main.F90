! WITH_QP comes from the build script's probe of this compiler.
program main
  implicit none
#if WITH_QP
  integer, parameter :: wp = selected_real_kind(33)
#else
  integer, parameter :: wp = selected_real_kind(15)
#endif
  real(wp) :: x = 2

  print '(a)', 'Built with the widest real kind this compiler has'
  print '(a, i0, a)', 'It has ', precision(x), ' decimal digits'
end program main
