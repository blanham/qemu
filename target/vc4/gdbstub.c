/*
 * VideoCore IV VPU GDB stub
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "gdbstub/helpers.h"
#include "cpu.h"

int vc4_cpu_gdb_read_register(CPUState *cs, GByteArray *buf, int reg)
{
    CPUVC4State *env = cpu_env(cs);

    if (reg < 0 || reg >= VC4_NUM_REGS) {
        return 0;
    }
    return gdb_get_reg32(buf, vc4_env_get_reg(env, reg));
}

int vc4_cpu_gdb_write_register(CPUState *cs, uint8_t *buf, int reg)
{
    CPUVC4State *env = cpu_env(cs);

    if (reg < 0 || reg >= VC4_NUM_REGS) {
        return 0;
    }
    vc4_env_set_reg(env, reg, ldl_le_p(buf));
    return 4;
}
