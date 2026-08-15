/*
 * VideoCore IV uncommon scalar floating-point helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "fpu/softfloat.h"
#include "exec/helper-proto-common.h"
#define HELPER_H "target/vc4/helper.h"
#include "exec/helper-proto.h.inc"
#undef HELPER_H

enum VC4FloatExtOp {
    VC4_FCEIL = 12,
    VC4_FFLOOR,
    VC4_FLOG2,
    VC4_FEXP2,
};

static void vc4_float_ext_status_init(float_status *status,
                                      FloatRoundMode rounding)
{
    memset(status, 0, sizeof(*status));
    set_float_rounding_mode(rounding, status);
    set_float_detect_tininess(float_tininess_after_rounding, status);
    set_default_nan_mode(true, status);
    set_float_default_nan_pattern(0x40, status);
    set_snan_rule(float_snan_bit_is_zero, status);
}

/*
 * QEMU's generic exp2 helper is polynomial based and can land one ULP below
 * an exact power of two.  VC4 firmware expects integer exponents to produce
 * exact powers, so recognize the exactly representable int32 subset and use
 * scalbn for that path.
 */
static bool vc4_float_exact_i32(float32 input, int32_t *value)
{
    float_status status;
    float32 roundtrip;
    int32_t converted;

    if (float32_is_any_nan(input) || float32_is_infinity(input)) {
        return false;
    }

    vc4_float_ext_status_init(&status, float_round_to_zero);
    converted = float32_to_int32_round_to_zero(input, &status);
    roundtrip = int32_to_float32(converted, &status);

    if (float32_is_zero(input) && float32_is_zero(roundtrip)) {
        *value = 0;
        return true;
    }
    if (float32_val(input) != float32_val(roundtrip)) {
        return false;
    }

    *value = converted;
    return true;
}

uint32_t helper_vc4_float_ext_op(uint32_t op, uint32_t b_bits)
{
    float_status status;
    float32 b = make_float32(b_bits);
    float32 result;
    int32_t exponent;

    vc4_float_ext_status_init(&status, float_round_nearest_even);

    switch (op) {
    case VC4_FCEIL:
        set_float_rounding_mode(float_round_up, &status);
        result = float32_round_to_int(b, &status);
        break;
    case VC4_FFLOOR:
        set_float_rounding_mode(float_round_down, &status);
        result = float32_round_to_int(b, &status);
        break;
    case VC4_FLOG2:
        result = float32_log2(b, &status);
        break;
    case VC4_FEXP2:
        if (vc4_float_exact_i32(b, &exponent)) {
            result = float32_scalbn(float32_one,
                                    CLAMP(exponent, -512, 512), &status);
        } else {
            result = float32_exp2(b, &status);
        }
        break;
    default:
        return 0;
    }

    return float32_val(result);
}
