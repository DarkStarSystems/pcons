/* SPDX-License-Identifier: MIT */
/* Board support for QEMU's lm3s6965evb: UART0 and a semihosting exit. */

#include "uart.h"

#include <stdint.h>

#define UART0_DR (*(volatile uint32_t *) 0x4000C000u)

static void uart_putc(char c)
{
    UART0_DR = (uint32_t) (unsigned char) c;
}

void uart_puts(const char *s)
{
    for (; *s; ++s) {
        if (*s == '\n')
            uart_putc('\r');
        uart_putc(*s);
    }
}

void semihost_exit(void)
{
    /* SYS_EXIT / ADP_Stopped_ApplicationExit: tells QEMU to quit with 0. */
    register uint32_t r0 __asm__("r0") = 0x18u;
    register uint32_t r1 __asm__("r1") = 0x20026u;
    __asm__ volatile("bkpt #0xAB" : : "r"(r0), "r"(r1) : "memory");
}
