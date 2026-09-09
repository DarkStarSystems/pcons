/* SPDX-License-Identifier: MIT */
/* Minimal Cortex-M startup: vector table, data/bss init, call main. */

#include <stdint.h>

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss, _estack;

int main(void);

void Reset_Handler(void)
{
    uint32_t *src = &_sidata;
    for (uint32_t *dst = &_sdata; dst < &_edata;)
        *dst++ = *src++;

    for (uint32_t *dst = &_sbss; dst < &_ebss;)
        *dst++ = 0;

    main();

    for (;;)
        ;
}

static void Default_Handler(void)
{
    for (;;)
        ;
}

__attribute__((section(".isr_vector"), used)) void (*const g_vectors[])(void) = {
    (void (*)(void)) & _estack,
    Reset_Handler,
    Default_Handler, /* NMI */
    Default_Handler, /* HardFault */
};
