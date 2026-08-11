/*
 * VideoCore IV VPU TCG helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

DEF_HELPER_FLAGS_3(complex_alu, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_4(div, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_FLAGS_4(mulhd, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_5(push_pop, void, env, i32, i32, i32, i32)
DEF_HELPER_3(raise_illegal, noreturn, env, i32, i32)
DEF_HELPER_1(halt, noreturn, env)
