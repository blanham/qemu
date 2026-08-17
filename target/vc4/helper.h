/*
 * VideoCore IV VPU TCG helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

DEF_HELPER_FLAGS_3(vc4_complex_alu, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_4(vc4_div, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_FLAGS_4(vc4_mulhd, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_FLAGS_3(vc4_float_conv, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_3(vc4_float_op, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_2(vc4_float_cmp, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32)
DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)
DEF_HELPER_1(vc4_rti, void, env)
DEF_HELPER_3(vc4_swi, noreturn, env, i32, i32)
DEF_HELPER_3(vc4_raise_illegal, noreturn, env, i32, i32)
DEF_HELPER_1(vc4_halt, noreturn, env)
