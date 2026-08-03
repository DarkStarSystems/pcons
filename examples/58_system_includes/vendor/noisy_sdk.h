/* SPDX-License-Identifier: MIT */
/* Stands in for a vendored third-party SDK header: correct, but written to a
 * different standard of tidiness than the project it's dropped into. Under
 * -Wall -Wextra -Werror this alone breaks the build -- unless it is included
 * as a *system* header. */

#ifndef NOISY_SDK_H
#define NOISY_SDK_H

/* -Wunused-parameter: the SDK keeps the parameter for API compatibility. */
static int sdk_answer(int reserved) { return 42; }

#endif /* NOISY_SDK_H */
