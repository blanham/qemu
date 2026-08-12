/*
 * VideoCore IV VPU TCG translator
 *
 * The scalar decoder follows the public VideoCore IV VPU encoding recovered
 * by the Raspberry Pi reverse-engineering community.  The vector ISA is
 * deliberately rejected for now rather than guessed.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/bitops.h"
#include "cpu.h"
#include "tcg/tcg-op.h"
#include "exec/helper-proto-common.h"
#define HELPER_H "target/vc4/helper.h"
#include "exec/helper-proto.h.inc"
#undef HELPER_H
#include "exec/helper-gen-common.h"
#define HELPER_H "target/vc4/helper.h"
#include "exec/helper-gen.h.inc"
#undef HELPER_H
#include "exec/translator.h"
#include "exec/translation-block.h"
#include "exec/log.h"

#define HELPER_H "target/vc4/helper.h"
#include "exec/helper-info.c.inc"
#undef HELPER_H

enum {
    DISAS_JUMP = DISAS_TARGET_0,
    DISAS_EXIT = DISAS_TARGET_1,
};

enum VC4AluOp {
    VC4_OP_MOV = 0,
    VC4_OP_CMN,
    VC4_OP_ADD,
    VC4_OP_BIC,
    VC4_OP_MUL,
    VC4_OP_EOR,
    VC4_OP_SUB,
    VC4_OP_AND,
    VC4_OP_MVN,
    VC4_OP_ROR,
    VC4_OP_CMP,
    VC4_OP_RSB,
    VC4_OP_BTST,
    VC4_OP_OR,
    VC4_OP_EXTU,
    VC4_OP_MAX,
    VC4_OP_BSET,
    VC4_OP_MIN,
    VC4_OP_BCLR,
    VC4_OP_ADDS2,
    VC4_OP_BCHG,
    VC4_OP_ADDS4,
    VC4_OP_ADDS8,
    VC4_OP_ADDS16,
    VC4_OP_EXTS,
    VC4_OP_NEG,
    VC4_OP_LSR,
    VC4_OP_CLZ,
    VC4_OP_LSL,
    VC4_OP_BREV,
    VC4_OP_ASR,
    VC4_OP_ABS,
};

enum VC4FloatConvOp {
    VC4_FCONV_FTRUNC = 0,
    VC4_FCONV_FLOOR,
    VC4_FCONV_FLTS,
    VC4_FCONV_FLTU,
};

enum VC4FloatOp {
    VC4_FOP_FADD = 0,
    VC4_FOP_FSUB,
    VC4_FOP_FMUL,
    VC4_FOP_FDIV,
    VC4_FOP_FCMP,
    VC4_FOP_FABS,
    VC4_FOP_FRSB,
    VC4_FOP_FMAX,
    VC4_FOP_FRCP,
    VC4_FOP_FRSQRT,
    VC4_FOP_FNMUL,
    VC4_FOP_FMIN,
    VC4_FOP_FLD1,
    VC4_FOP_FLD0,
};

typedef struct DisasContext {
    DisasContextBase base;
    CPUVC4State *env;
    uint32_t pc;
} DisasContext;

static TCGv_i32 cpu_gpr[VC4_NUM_GPRS];
static TCGv_i32 cpu_sr;
static TCGv_i32 cpu_pc;

static int32_t vc4_sext(uint32_t value, uint32_t sign_bit)
{
    return (value ^ sign_bit) - sign_bit;
}

static uint16_t vc4_lduw(DisasContext *ctx, vaddr addr)
{
    return translator_lduw_end((CPUArchState *)(void *)ctx->env,
                               &ctx->base, addr, MO_LE);
}

static TCGv_i32 vc4_get_reg(DisasContext *ctx, unsigned reg)
{
    if (reg < VC4_NUM_GPRS) {
        return cpu_gpr[reg];
    }
    if (reg == VC4_REG_SR) {
        return cpu_sr;
    }
    return tcg_constant_i32(ctx->pc);
}

static void vc4_set_reg(DisasContext *ctx, unsigned reg, TCGv_i32 value)
{
    if (reg < VC4_NUM_GPRS) {
        tcg_gen_mov_i32(cpu_gpr[reg], value);
    } else if (reg == VC4_REG_SR) {
        tcg_gen_mov_i32(cpu_sr, value);
    } else {
        tcg_gen_mov_i32(cpu_pc, value);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

static void vc4_set_reg_imm(DisasContext *ctx, unsigned reg, uint32_t value)
{
    if (reg < VC4_NUM_GPRS) {
        tcg_gen_movi_i32(cpu_gpr[reg], value);
    } else if (reg == VC4_REG_SR) {
        tcg_gen_movi_i32(cpu_sr, value);
    } else {
        tcg_gen_movi_i32(cpu_pc, value);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

static TCGv_i32 vc4_gen_cond_from_sr(TCGv_i32 sr, unsigned cond)
{
    TCGv_i32 v = tcg_temp_new_i32();
    TCGv_i32 c = tcg_temp_new_i32();
    TCGv_i32 n = tcg_temp_new_i32();
    TCGv_i32 z = tcg_temp_new_i32();
    TCGv_i32 result = tcg_temp_new_i32();
    TCGv_i32 tmp = tcg_temp_new_i32();

    tcg_gen_extract_i32(v, sr, 0, 1);
    tcg_gen_extract_i32(c, sr, 1, 1);
    tcg_gen_extract_i32(n, sr, 2, 1);
    tcg_gen_extract_i32(z, sr, 3, 1);

    switch (cond >> 1) {
    case 0:                         /* EQ */
        tcg_gen_mov_i32(result, z);
        break;
    case 1:                         /* CS */
        tcg_gen_mov_i32(result, c);
        break;
    case 2:                         /* NS */
        tcg_gen_mov_i32(result, n);
        break;
    case 3:                         /* VS */
        tcg_gen_mov_i32(result, v);
        break;
    case 4:                         /* HI: !C && !Z */
        tcg_gen_or_i32(tmp, c, z);
        tcg_gen_setcondi_i32(TCG_COND_EQ, result, tmp, 0);
        break;
    case 5:                         /* GE: N == V */
        tcg_gen_xor_i32(tmp, n, v);
        tcg_gen_setcondi_i32(TCG_COND_EQ, result, tmp, 0);
        break;
    case 6:                         /* GT: N == V && !Z */
        tcg_gen_xor_i32(tmp, n, v);
        tcg_gen_or_i32(tmp, tmp, z);
        tcg_gen_setcondi_i32(TCG_COND_EQ, result, tmp, 0);
        break;
    case 7:                         /* always */
        tcg_gen_movi_i32(result, 1);
        break;
    default:
        g_assert_not_reached();
    }

    if (cond & 1) {
        tcg_gen_xori_i32(result, result, 1);
    }
    return result;
}

static TCGLabel *vc4_gen_skip_if_false(unsigned cond)
{
    TCGLabel *skip;
    TCGv_i32 predicate;

    if (cond == 14) {
        return NULL;
    }

    skip = gen_new_label();
    if (cond == 15) {
        tcg_gen_br(skip);
        return skip;
    }

    predicate = vc4_gen_cond_from_sr(cpu_sr, cond);
    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, skip);
    return skip;
}

static void vc4_gen_end_predicate(TCGLabel *skip)
{
    if (skip) {
        gen_set_label(skip);
    }
}

static TCGv_i32 vc4_gen_sub_flags(TCGv_i32 a, TCGv_i32 b)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGv_i32 flags = tcg_temp_new_i32();
    TCGv_i32 bit = tcg_temp_new_i32();
    TCGv_i32 tmp = tcg_temp_new_i32();

    tcg_gen_sub_i32(result, a, b);
    tcg_gen_setcondi_i32(TCG_COND_EQ, flags, result, 0);
    tcg_gen_shli_i32(flags, flags, 3);              /* Z */

    tcg_gen_shri_i32(bit, result, 31);
    tcg_gen_shli_i32(bit, bit, 2);                  /* N */
    tcg_gen_or_i32(flags, flags, bit);

    tcg_gen_setcond_i32(TCG_COND_LTU, bit, a, b);   /* C == borrow */
    tcg_gen_shli_i32(bit, bit, 1);
    tcg_gen_or_i32(flags, flags, bit);

    tcg_gen_xor_i32(tmp, a, b);
    tcg_gen_xor_i32(bit, a, result);
    tcg_gen_and_i32(bit, bit, tmp);
    tcg_gen_shri_i32(bit, bit, 31);                 /* V */
    tcg_gen_or_i32(flags, flags, bit);

    return flags;
}

static TCGv_i32 vc4_gen_add_flags(TCGv_i32 a, TCGv_i32 b)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGv_i32 flags = tcg_temp_new_i32();
    TCGv_i32 bit = tcg_temp_new_i32();
    TCGv_i32 tmp = tcg_temp_new_i32();

    tcg_gen_add_i32(result, a, b);
    tcg_gen_setcondi_i32(TCG_COND_EQ, flags, result, 0);
    tcg_gen_shli_i32(flags, flags, 3);              /* Z */

    tcg_gen_shri_i32(bit, result, 31);
    tcg_gen_shli_i32(bit, bit, 2);                  /* N */
    tcg_gen_or_i32(flags, flags, bit);

    tcg_gen_setcond_i32(TCG_COND_LTU, bit, result, a);
    tcg_gen_shli_i32(bit, bit, 1);                  /* C == carry */
    tcg_gen_or_i32(flags, flags, bit);

    tcg_gen_xor_i32(tmp, a, b);
    tcg_gen_not_i32(tmp, tmp);
    tcg_gen_xor_i32(bit, a, result);
    tcg_gen_and_i32(bit, bit, tmp);
    tcg_gen_shri_i32(bit, bit, 31);                 /* V */
    tcg_gen_or_i32(flags, flags, bit);

    return flags;
}

static void vc4_write_nzcv(TCGv_i32 flags)
{
    TCGv_i32 upper = tcg_temp_new_i32();

    tcg_gen_andi_i32(upper, cpu_sr, ~0xfu);
    tcg_gen_or_i32(cpu_sr, upper, flags);
}

static void vc4_gen_alu(DisasContext *ctx, unsigned cond, unsigned op,
                        unsigned rd, TCGv_i32 a, TCGv_i32 b)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGv_i32 tmp = tcg_temp_new_i32();
    TCGLabel *skip;
    bool writes_result = true;

    /*
     * A predicated write to PC must leave the fall-through address in PC
     * when the predicate fails.
     */
    if (rd == VC4_REG_PC && op != VC4_OP_CMN &&
        op != VC4_OP_CMP && op != VC4_OP_BTST) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);

    switch (op) {
    case VC4_OP_MOV:
        tcg_gen_mov_i32(result, b);
        break;
    case VC4_OP_CMN:
        vc4_write_nzcv(vc4_gen_add_flags(a, b));
        writes_result = false;
        break;
    case VC4_OP_ADD:
        tcg_gen_add_i32(result, a, b);
        break;
    case VC4_OP_BIC:
        tcg_gen_andc_i32(result, a, b);
        break;
    case VC4_OP_MUL:
        tcg_gen_mul_i32(result, a, b);
        break;
    case VC4_OP_EOR:
        tcg_gen_xor_i32(result, a, b);
        break;
    case VC4_OP_SUB:
        tcg_gen_sub_i32(result, a, b);
        break;
    case VC4_OP_AND:
        tcg_gen_and_i32(result, a, b);
        break;
    case VC4_OP_MVN:
        tcg_gen_not_i32(result, b);
        break;
    case VC4_OP_CMP:
        vc4_write_nzcv(vc4_gen_sub_flags(a, b));
        writes_result = false;
        break;
    case VC4_OP_RSB:
        tcg_gen_sub_i32(result, b, a);
        break;
    case VC4_OP_BTST:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_movi_i32(result, 1);
        tcg_gen_shl_i32(result, result, tmp);
        tcg_gen_and_i32(result, result, a);
        tcg_gen_setcondi_i32(TCG_COND_EQ, result, result, 0);
        tcg_gen_andi_i32(tmp, cpu_sr, ~VC4_SR_Z);
        tcg_gen_shli_i32(result, result, 3);
        tcg_gen_or_i32(cpu_sr, tmp, result);
        writes_result = false;
        break;
    case VC4_OP_OR:
        tcg_gen_or_i32(result, a, b);
        break;
    case VC4_OP_ADDS2:
        tcg_gen_shli_i32(result, b, 1);
        tcg_gen_add_i32(result, result, a);
        break;
    case VC4_OP_ADDS4:
        tcg_gen_shli_i32(result, b, 2);
        tcg_gen_add_i32(result, result, a);
        break;
    case VC4_OP_ADDS8:
        tcg_gen_shli_i32(result, b, 3);
        tcg_gen_add_i32(result, result, a);
        break;
    case VC4_OP_ADDS16:
        tcg_gen_shli_i32(result, b, 4);
        tcg_gen_add_i32(result, result, a);
        break;
    case VC4_OP_NEG:
        tcg_gen_neg_i32(result, b);
        break;
    case VC4_OP_LSR:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_shr_i32(result, a, tmp);
        break;
    case VC4_OP_LSL:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_shl_i32(result, a, tmp);
        break;
    case VC4_OP_ASR:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_sar_i32(result, a, tmp);
        break;
    default:
        gen_helper_vc4_complex_alu(result, tcg_constant_i32(op), a, b);
        break;
    }

    if (writes_result) {
        vc4_set_reg(ctx, rd, result);
    }
    vc4_gen_end_predicate(skip);
}

static void vc4_gen_alu_regs(DisasContext *ctx, unsigned cond, unsigned op,
                             unsigned rd, unsigned ra, unsigned rb)
{
    vc4_gen_alu(ctx, cond, op, rd,
                vc4_get_reg(ctx, ra), vc4_get_reg(ctx, rb));
}

static void vc4_gen_alu_imm(DisasContext *ctx, unsigned cond, unsigned op,
                            unsigned rd, unsigned ra, int32_t imm)
{
    vc4_gen_alu(ctx, cond, op, rd, vc4_get_reg(ctx, ra),
                tcg_constant_i32(imm));
}

static void vc4_gen_float_conv(DisasContext *ctx, unsigned cond,
                               unsigned op, unsigned rd, unsigned ra,
                               int32_t shift)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGLabel *skip;

    if (rd == VC4_REG_PC) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);
    gen_helper_vc4_float_conv(result, tcg_constant_i32(op),
                              vc4_get_reg(ctx, ra),
                              tcg_constant_i32(shift));
    vc4_set_reg(ctx, rd, result);
    vc4_gen_end_predicate(skip);
}

static bool vc4_gen_float_op(DisasContext *ctx, unsigned cond,
                             unsigned op, unsigned rd,
                             unsigned ra, TCGv_i32 b)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGLabel *skip;

    if (op > VC4_FOP_FLD0) {
        return false;
    }

    if (op != VC4_FOP_FCMP && rd == VC4_REG_PC) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);
    if (op == VC4_FOP_FCMP) {
        gen_helper_vc4_float_cmp(result, vc4_get_reg(ctx, ra), b);
        vc4_write_nzcv(result);
    } else {
        gen_helper_vc4_float_op(result, tcg_constant_i32(op),
                                vc4_get_reg(ctx, ra), b);
        vc4_set_reg(ctx, rd, result);
    }
    vc4_gen_end_predicate(skip);
    return true;
}

static unsigned vc4_mem_size(unsigned format)
{
    static const unsigned size[4] = { 4, 2, 1, 2 };

    return size[format & 3];
}

static MemOp vc4_load_mop(unsigned format)
{
    static const MemOp mop[4] = {
        MO_UL | MO_LE,
        MO_UW | MO_LE,
        MO_UB,
        MO_SW | MO_LE,
    };

    return mop[format & 3];
}

static MemOp vc4_store_mop(unsigned format)
{
    static const MemOp mop[4] = {
        MO_32 | MO_LE,
        MO_16 | MO_LE,
        MO_8,
        MO_16 | MO_LE,
    };

    return mop[format & 3];
}

static void vc4_gen_qemu_st_i32(TCGv_i32 value, TCGv_i32 address,
                                  TCGArg mmu_idx, MemOp memop)
{
#if TARGET_LONG_BITS == 64
    TCGv_i64 wide = tcg_temp_new_i64();

    tcg_gen_extu_i32_i64(wide, address);
    tcg_gen_qemu_st_i32(value, wide, mmu_idx, memop);
#else
    tcg_gen_qemu_st_i32(value, address, mmu_idx, memop);
#endif
}

static void vc4_gen_qemu_ld_i32(TCGv_i32 value, TCGv_i32 address,
                                  TCGArg mmu_idx, MemOp memop)
{
#if TARGET_LONG_BITS == 64
    TCGv_i64 wide = tcg_temp_new_i64();

    tcg_gen_extu_i32_i64(wide, address);
    tcg_gen_qemu_ld_i32(value, wide, mmu_idx, memop);
#else
    tcg_gen_qemu_ld_i32(value, address, mmu_idx, memop);
#endif
}

static void vc4_gen_load_store_addr(DisasContext *ctx, unsigned cond,
                                    bool store, unsigned format, unsigned rd,
                                    TCGv_i32 address)
{
    TCGLabel *skip;
    TCGv_i32 value = tcg_temp_new_i32();

    if (!store && rd == VC4_REG_PC) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);
    if (store) {
        vc4_gen_qemu_st_i32(vc4_get_reg(ctx, rd), address, 0,
                            vc4_store_mop(format));
    } else {
        vc4_gen_qemu_ld_i32(value, address, 0, vc4_load_mop(format));
        vc4_set_reg(ctx, rd, value);
    }
    vc4_gen_end_predicate(skip);
}

static void vc4_gen_load_store_offset(DisasContext *ctx, unsigned cond,
                                      bool store, unsigned format,
                                      unsigned rd, unsigned rb,
                                      int32_t offset, bool postincrement,
                                      bool predecrement)
{
    TCGLabel *skip;
    TCGv_i32 address = tcg_temp_new_i32();
    TCGv_i32 value = tcg_temp_new_i32();
    TCGv_i32 updated = tcg_temp_new_i32();
    unsigned size = vc4_mem_size(format);

    if (!store && rd == VC4_REG_PC) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);

    if (predecrement) {
        tcg_gen_subi_i32(updated, vc4_get_reg(ctx, rb), size);
        vc4_set_reg(ctx, rb, updated);
        tcg_gen_addi_i32(address, updated, offset);
    } else {
        tcg_gen_addi_i32(address, vc4_get_reg(ctx, rb), offset);
    }

    if (store) {
        vc4_gen_qemu_st_i32(vc4_get_reg(ctx, rd), address, 0,
                            vc4_store_mop(format));
    } else {
        vc4_gen_qemu_ld_i32(value, address, 0, vc4_load_mop(format));
        vc4_set_reg(ctx, rd, value);
    }

    if (postincrement) {
        tcg_gen_addi_i32(updated, vc4_get_reg(ctx, rb), size);
        vc4_set_reg(ctx, rb, updated);
    }

    vc4_gen_end_predicate(skip);
}

static void vc4_gen_load_store_indexed(DisasContext *ctx, unsigned cond,
                                       bool store, unsigned format,
                                       unsigned rd, unsigned ra,
                                       unsigned rb)
{
    TCGv_i32 address = tcg_temp_new_i32();
    TCGv_i32 index = tcg_temp_new_i32();
    unsigned size = vc4_mem_size(format);

    if (size == 1) {
        tcg_gen_mov_i32(index, vc4_get_reg(ctx, rb));
    } else {
        tcg_gen_shli_i32(index, vc4_get_reg(ctx, rb),
                         size == 2 ? 1 : 2);
    }
    tcg_gen_add_i32(address, vc4_get_reg(ctx, ra), index);
    vc4_gen_load_store_addr(ctx, cond, store, format, rd, address);
}

static void vc4_gen_goto_tb(DisasContext *ctx, unsigned slot, uint32_t dest)
{
    if (translator_use_goto_tb(&ctx->base, dest)) {
        tcg_gen_goto_tb(slot);
        tcg_gen_movi_i32(cpu_pc, dest);
        tcg_gen_exit_tb(ctx->base.tb, slot);
    } else {
        tcg_gen_movi_i32(cpu_pc, dest);
        tcg_gen_lookup_and_goto_ptr();
    }
    ctx->base.is_jmp = DISAS_NORETURN;
}

static void vc4_gen_cond_branch(DisasContext *ctx, unsigned cond,
                                uint32_t dest)
{
    TCGv_i32 predicate;
    TCGLabel *not_taken;
    uint32_t next = ctx->base.pc_next;

    if (cond == 14) {
        vc4_gen_goto_tb(ctx, 0, dest);
        return;
    }
    if (cond == 15) {
        vc4_gen_goto_tb(ctx, 0, next);
        return;
    }

    predicate = vc4_gen_cond_from_sr(cpu_sr, cond);
    not_taken = gen_new_label();

    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
    tcg_gen_movi_i32(cpu_pc, dest);
    tcg_gen_exit_tb(NULL, 0);

    gen_set_label(not_taken);
    tcg_gen_movi_i32(cpu_pc, next);
    tcg_gen_exit_tb(NULL, 0);
    ctx->base.is_jmp = DISAS_NORETURN;
}

static void vc4_gen_cmp_branch(DisasContext *ctx, unsigned cond,
                               TCGv_i32 a, TCGv_i32 b, uint32_t dest)
{
    TCGv_i32 flags = vc4_gen_sub_flags(a, b);
    TCGv_i32 predicate;
    TCGLabel *not_taken;
    uint32_t next = ctx->base.pc_next;

    if (cond == 14) {
        vc4_gen_goto_tb(ctx, 0, dest);
        return;
    }
    if (cond == 15) {
        vc4_gen_goto_tb(ctx, 0, next);
        return;
    }

    predicate = vc4_gen_cond_from_sr(flags, cond);
    not_taken = gen_new_label();

    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
    tcg_gen_movi_i32(cpu_pc, dest);
    tcg_gen_exit_tb(NULL, 0);

    gen_set_label(not_taken);
    tcg_gen_movi_i32(cpu_pc, next);
    tcg_gen_exit_tb(NULL, 0);
    ctx->base.is_jmp = DISAS_NORETURN;
}

static void vc4_gen_illegal(DisasContext *ctx, uint16_t opcode)
{
    gen_helper_vc4_raise_illegal(tcg_env, tcg_constant_i32(ctx->pc),
                             tcg_constant_i32(opcode));
    ctx->base.is_jmp = DISAS_NORETURN;
}

static bool vc4_decode_scalar16(DisasContext *ctx, uint16_t insn)
{
    unsigned op, rd, rs, format;
    int32_t offset;

    switch (insn) {
    case 0x0000:                    /* BKPT/HALT */
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        gen_helper_vc4_halt(tcg_env);
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    case 0x0001:                    /* NOP */
        return true;
    case 0x0002:                    /* SLEEP */
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        gen_helper_vc4_halt(tcg_env);
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    case 0x0003:                    /* USER */
        tcg_gen_ori_i32(cpu_sr, cpu_sr, VC4_SR_U);
        return true;
    case 0x0004:                    /* EI */
        tcg_gen_ori_i32(cpu_sr, cpu_sr, VC4_SR_I);
        return true;
    case 0x0005:                    /* DI */
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~VC4_SR_I);
        return true;
    case 0x0006:                    /* CBCLR */
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~VC4_SR_CB_MASK);
        return true;
    case 0x0007:
    case 0x0008:
    case 0x0009: {                  /* CBADD1/2/3 */
        TCGv_i32 cb = tcg_temp_new_i32();

        tcg_gen_extract_i32(cb, cpu_sr, 4, 2);
        tcg_gen_addi_i32(cb, cb, insn - 0x0006);
        tcg_gen_andi_i32(cb, cb, 3);
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~VC4_SR_CB_MASK);
        tcg_gen_shli_i32(cb, cb, 4);
        tcg_gen_or_i32(cpu_sr, cpu_sr, cb);
        return true;
    }
    case 0x000a:                    /* RTI */
        gen_helper_vc4_rti(tcg_env);
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    default:
        break;
    }

    if ((insn & 0xffe0) == 0x0040) {
        tcg_gen_mov_i32(cpu_pc, vc4_get_reg(ctx, insn & 0x1f));
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
    if ((insn & 0xffe0) == 0x0060) {
        tcg_gen_movi_i32(cpu_gpr[VC4_REG_LR], ctx->base.pc_next);
        tcg_gen_mov_i32(cpu_pc, vc4_get_reg(ctx, insn & 0x1f));
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
    if ((insn & 0xffe0) == 0x0080 || (insn & 0xffe0) == 0x00a0) {
        return false;               /* TBB/TBH */
    }
    if ((insn & 0xffe0) == 0x00e0) {
        vc4_set_reg_imm(ctx, insn & 0x1f, VC4_CPUID_VALUE);
        return true;
    }

    if ((insn & 0xf800) == 0x1800) {
        unsigned cond = (insn >> 7) & 0xf;

        offset = vc4_sext(insn & 0x7f, 0x40) * 2;
        vc4_gen_cond_branch(ctx, cond, ctx->pc + offset);
        return true;
    }

    if ((insn & 0xf800) == 0x1000) {
        rd = insn & 0x1f;
        offset = vc4_sext((insn >> 5) & 0x3f, 0x20) * 4;
        vc4_gen_alu_imm(ctx, 14, VC4_OP_ADD, rd, VC4_REG_SP, offset);
        return true;
    }

    if ((insn & 0xfe00) == 0x0200) {
        static const unsigned start_reg[4] = { 0, 6, 16, 24 };
        bool push = (insn & 0x80) != 0;
        bool lrpc = (insn & 0x100) != 0;
        unsigned start = start_reg[(insn >> 5) & 3];
        unsigned count = (insn & 0x1f) + 1;
        bool writes_pc = !push &&
            (lrpc || (start <= VC4_REG_PC &&
                      start + count > VC4_REG_PC));

        gen_helper_vc4_push_pop(tcg_env,
                            tcg_constant_i32(push),
                            tcg_constant_i32(lrpc),
                            tcg_constant_i32(start),
                            tcg_constant_i32(count));
        if (writes_pc) {
            ctx->base.is_jmp = DISAS_JUMP;
        }
        return true;
    }

    if ((insn & 0xfc00) == 0x0400) {
        bool store = (insn & 0x200) != 0;

        rd = insn & 0xf;
        offset = vc4_sext((insn >> 4) & 0x1f, 0x10) * 4;
        vc4_gen_load_store_offset(ctx, 14, store, 0, rd, VC4_REG_SP,
                                  offset, false, false);
        return true;
    }

    if ((insn & 0xf800) == 0x0800) {
        bool store = (insn & 0x100) != 0;

        format = (insn >> 9) & 3;
        rd = insn & 0xf;
        rs = (insn >> 4) & 0xf;
        vc4_gen_load_store_offset(ctx, 14, store, format, rd, rs,
                                  0, false, false);
        return true;
    }

    if ((insn & 0xe000) == 0x2000) {
        bool store = (insn & 0x1000) != 0;

        rd = insn & 0xf;
        rs = (insn >> 4) & 0xf;
        offset = ((insn >> 8) & 0xf) * 4;
        vc4_gen_load_store_offset(ctx, 14, store, 0, rd, rs,
                                  offset, false, false);
        return true;
    }

    if ((insn & 0xe000) == 0x4000) {
        op = (insn >> 8) & 0x1f;
        rd = insn & 0xf;
        rs = (insn >> 4) & 0xf;
        vc4_gen_alu_regs(ctx, 14, op, rd, rd, rs);
        return true;
    }

    if ((insn & 0xe000) == 0x6000) {
        op = ((insn >> 9) & 0xf) * 2;
        rd = insn & 0xf;
        vc4_gen_alu_imm(ctx, 14, op, rd, rd, (insn >> 4) & 0x1f);
        return true;
    }

    return false;
}

static bool vc4_decode_scalar32(DisasContext *ctx, uint16_t i1, uint16_t i2)
{
    unsigned cond, op, rd, ra, rb, format;
    uint32_t raw;
    int32_t offset;

    if ((i1 & 0xf080) == 0x9000) {
        cond = (i1 >> 8) & 0xf;
        raw = i2 | ((uint32_t)(i1 & 0x7f) << 16);
        offset = vc4_sext(raw, 0x00400000) * 2;
        vc4_gen_cond_branch(ctx, cond, ctx->pc + offset);
        return true;
    }

    if ((i1 & 0xf080) == 0x9080) {
        raw = i2 | ((uint32_t)(i1 & 0x7f) << 16);
        raw |= (uint32_t)(i1 & 0xf00) << 15;
        offset = vc4_sext(raw, 0x04000000) * 2;
        tcg_gen_movi_i32(cpu_gpr[VC4_REG_LR], ctx->base.pc_next);
        vc4_gen_goto_tb(ctx, 0, ctx->pc + offset);
        return true;
    }

    if ((i1 & 0xff80) == 0xca00 && (i2 & 0x0040) != 0) {
        cond = (i2 >> 7) & 0xf;
        op = (i1 >> 5) & 3;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        offset = vc4_sext(i2 & 0x3f, 0x20);
        vc4_gen_float_conv(ctx, cond, op, rd, ra, offset);
        return true;
    }

    if ((i1 & 0xfe00) == 0xc800 && (i2 & 0x0060) == 0x0000) {
        cond = (i2 >> 7) & 0xf;
        op = (i1 >> 5) & 0xf;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        return vc4_gen_float_op(ctx, cond, op, rd, ra,
                                vc4_get_reg(ctx, rb));
    }

    if ((i1 & 0xfe00) == 0xc800 && (i2 & 0x0040) != 0) {
        /* The six-bit floating immediate encoding is not verified yet. */
        return false;
    }

    if ((i1 & 0xfc00) == 0xc000 && (i2 & 0x0060) == 0x0000) {
        op = (i1 >> 5) & 0x1f;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        cond = (i2 >> 7) & 0xf;
        vc4_gen_alu_regs(ctx, cond, op, rd, ra, rb);
        return true;
    }

    if ((i1 & 0xfc00) == 0xc000 && (i2 & 0x0040) == 0x0040) {
        op = (i1 >> 5) & 0x1f;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        offset = vc4_sext(i2 & 0x3f, 0x20);
        cond = (i2 >> 7) & 0xf;
        vc4_gen_alu_imm(ctx, cond, op, rd, ra, offset);
        return true;
    }

    if ((i1 & 0xfc00) == 0xa800) {
        static const unsigned base_reg[4] = {
            24, VC4_REG_SP, VC4_REG_PC, 0
        };
        bool store = (i1 & 0x20) != 0;

        rb = base_reg[(i1 >> 8) & 3];
        format = (i1 >> 6) & 3;
        rd = i1 & 0x1f;
        offset = (int16_t)i2;
        vc4_gen_load_store_offset(ctx, 14, store, format, rd, rb,
                                  offset, false, false);
        return true;
    }

    if ((i1 & 0xff00) == 0xa000 && (i2 & 0x0060) == 0x0000) {
        bool store = (i1 & 0x20) != 0;

        format = (i1 >> 6) & 3;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        cond = (i2 >> 7) & 0xf;
        vc4_gen_load_store_indexed(ctx, cond, store, format, rd, ra, rb);
        return true;
    }

    if ((i1 & 0xfe00) == 0xa200) {
        bool store = (i1 & 0x20) != 0;

        rd = i1 & 0x1f;
        rb = i2 >> 11;
        raw = (i2 & 0x7ff) | ((i1 << 3) & 0x800);
        offset = vc4_sext(raw, 0x800);
        format = (i1 >> 6) & 3;
        vc4_gen_load_store_offset(ctx, 14, store, format, rd, rb,
                                  offset, false, false);
        return true;
    }

    if ((i1 & 0xfe00) == 0xa400) {
        bool store = (i1 & 0x20) != 0;
        bool postincrement = (i1 & 0x100) != 0;

        rd = i1 & 0x1f;
        rb = i2 >> 11;
        format = (i1 >> 6) & 3;
        cond = (i2 >> 7) & 0xf;
        vc4_gen_load_store_offset(ctx, cond, store, format, rd, rb, 0,
                                  postincrement, !postincrement);
        return true;
    }

    if ((i1 & 0xfc00) == 0xb000) {
        op = (i1 >> 5) & 0x1f;
        rd = i1 & 0x1f;
        vc4_gen_alu_imm(ctx, 14, op, rd, rd, i2);
        return true;
    }

    if ((i1 & 0xfc00) == 0xb400) {
        rd = i1 & 0x1f;
        ra = (i1 >> 5) & 0x1f;
        vc4_gen_alu_imm(ctx, 14, VC4_OP_ADD, rd, ra, (int16_t)i2);
        return true;
    }

    if ((i1 & 0xffe0) == 0xbfe0) {
        rd = i1 & 0x1f;
        vc4_gen_alu_imm(ctx, 14, VC4_OP_ADD, rd, VC4_REG_PC,
                        (int16_t)i2);
        return true;
    }

    if ((i1 & 0xf000) == 0x8000) {
        TCGv_i32 compare_value;

        cond = (i1 >> 8) & 0xf;
        rd = i1 & 0xf;

        if (i2 & 0x4000) {
            vc4_gen_alu_imm(ctx, 14, VC4_OP_ADD, rd, rd,
                            vc4_sext((i1 >> 4) & 0xf, 0x8));
        } else {
            vc4_gen_alu_regs(ctx, 14, VC4_OP_ADD, rd, rd,
                             (i1 >> 4) & 0xf);
        }

        if (i2 & 0x8000) {
            offset = vc4_sext(i2 & 0xff, 0x80) * 2;
            compare_value = tcg_constant_i32((i2 >> 8) & 0x3f);
        } else {
            offset = vc4_sext(i2 & 0x3ff, 0x200) * 2;
            compare_value = vc4_get_reg(ctx, (i2 >> 10) & 0xf);
        }
        vc4_gen_cmp_branch(ctx, cond, vc4_get_reg(ctx, rd),
                           compare_value, ctx->pc + offset);
        return true;
    }

    if ((i1 & 0xff80) == 0xc480 && (i2 & 0x20) == 0) {
        TCGLabel *skip;
        TCGv_i32 result = tcg_temp_new_i32();
        bool a_unsigned = (i1 & 0x40) != 0;
        bool b_unsigned = (i1 & 0x20) != 0;

        if (i2 & 0x40) {
            return false;
        }
        cond = (i2 >> 7) & 0xf;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        if (rd == VC4_REG_PC) {
            tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        }
        skip = vc4_gen_skip_if_false(cond);
        gen_helper_vc4_div(result, vc4_get_reg(ctx, ra), vc4_get_reg(ctx, rb),
                       tcg_constant_i32(a_unsigned),
                       tcg_constant_i32(b_unsigned));
        vc4_set_reg(ctx, rd, result);
        vc4_gen_end_predicate(skip);
        return true;
    }

    if ((i1 & 0xffe0) == 0xc5e0 && (i2 & 0x60) == 0) {
        TCGLabel *skip;
        TCGv_i32 result = tcg_temp_new_i32();
        TCGv_i32 shifted = tcg_temp_new_i32();

        cond = (i2 >> 7) & 0xf;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        if (rd == VC4_REG_PC) {
            tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        }
        skip = vc4_gen_skip_if_false(cond);
        tcg_gen_shli_i32(shifted, vc4_get_reg(ctx, rb), 8);
        tcg_gen_add_i32(result, vc4_get_reg(ctx, ra), shifted);
        vc4_set_reg(ctx, rd, result);
        vc4_gen_end_predicate(skip);
        return true;
    }

    if ((i1 & 0xff80) == 0xc400 && (i2 & 0x60) == 0) {
        TCGLabel *skip;
        TCGv_i32 result = tcg_temp_new_i32();
        bool a_unsigned = (i1 & 0x40) != 0;
        bool b_unsigned = (i1 & 0x20) != 0;

        cond = (i2 >> 7) & 0xf;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        if (rd == VC4_REG_PC) {
            tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        }
        skip = vc4_gen_skip_if_false(cond);
        gen_helper_vc4_mulhd(result, vc4_get_reg(ctx, ra), vc4_get_reg(ctx, rb),
                         tcg_constant_i32(a_unsigned),
                         tcg_constant_i32(b_unsigned));
        vc4_set_reg(ctx, rd, result);
        vc4_gen_end_predicate(skip);
        return true;
    }

    return false;
}

static bool vc4_decode_scalar48(DisasContext *ctx, uint16_t i1,
                                uint16_t i2, uint16_t i3)
{
    unsigned op, rd, rs, format;
    uint32_t imm = i2 | ((uint32_t)i3 << 16);
    uint32_t raw;
    int32_t offset;

    if ((i1 & 0xfc00) == 0xe800) {
        op = (i1 >> 5) & 0x1f;
        rd = i1 & 0x1f;
        vc4_gen_alu_imm(ctx, 14, op, rd, rd, imm);
        return true;
    }

    if ((i1 & 0xffe0) == 0xe500) {
        rd = i1 & 0x1f;
        vc4_gen_alu_imm(ctx, 14, VC4_OP_ADD, rd, VC4_REG_PC, imm);
        return true;
    }

    if ((i1 & 0xff00) == 0xe600) {
        bool store = (i1 & 0x20) != 0;

        format = (i1 >> 6) & 3;
        rd = i1 & 0x1f;
        rs = i3 >> 11;
        raw = (i3 & 0x7ff) | ((uint32_t)i2 << 11);
        offset = vc4_sext(raw, 0x04000000);
        vc4_gen_load_store_offset(ctx, 14, store, format, rd, rs,
                                  offset, false, false);
        return true;
    }

    if ((i1 & 0xfc00) == 0xec00) {
        rs = (i1 >> 5) & 0x1f;
        rd = i1 & 0x1f;
        vc4_gen_alu_imm(ctx, 14, VC4_OP_ADD, rd, rs, imm);
        return true;
    }

    return false;
}

static void vc4_tr_init_disas_context(DisasContextBase *dcbase, CPUState *cs)
{
    DisasContext *ctx = container_of(dcbase, DisasContext, base);

    ctx->env = vc4_cpu_env(cs);
}

static void vc4_tr_tb_start(DisasContextBase *dcbase, CPUState *cs)
{
}

static void vc4_tr_insn_start(DisasContextBase *dcbase, CPUState *cs)
{
    DisasContext *ctx = container_of(dcbase, DisasContext, base);

    tcg_gen_insn_start(ctx->base.pc_next, 0, 0);
}

static void vc4_tr_translate_insn(DisasContextBase *dcbase, CPUState *cs)
{
    DisasContext *ctx = container_of(dcbase, DisasContext, base);
    uint16_t i1, i2, i3;
    bool decoded;

    ctx->pc = ctx->base.pc_next;
    i1 = vc4_lduw(ctx, ctx->pc);

    if (!(i1 & 0x8000)) {
        ctx->base.pc_next = ctx->pc + 2;
        decoded = vc4_decode_scalar16(ctx, i1);
    } else if ((i1 & 0xe000) != 0xe000) {
        i2 = vc4_lduw(ctx, ctx->pc + 2);
        ctx->base.pc_next = ctx->pc + 4;
        decoded = vc4_decode_scalar32(ctx, i1, i2);
    } else if ((i1 & 0xf000) == 0xe000) {
        i2 = vc4_lduw(ctx, ctx->pc + 2);
        i3 = vc4_lduw(ctx, ctx->pc + 4);
        ctx->base.pc_next = ctx->pc + 6;
        decoded = vc4_decode_scalar48(ctx, i1, i2, i3);
    } else {
        /* Vector48 (0xf000) and Vector80 (0xf800) are separate work. */
        ctx->base.pc_next = ctx->pc + ((i1 & 0xf800) == 0xf800 ? 10 : 6);
        decoded = false;
    }

    if (!decoded) {
        vc4_gen_illegal(ctx, i1);
    }
}

static void vc4_tr_tb_stop(DisasContextBase *dcbase, CPUState *cs)
{
    DisasContext *ctx = container_of(dcbase, DisasContext, base);

    switch (ctx->base.is_jmp) {
    case DISAS_NEXT:
    case DISAS_TOO_MANY:
        vc4_gen_goto_tb(ctx, 0, ctx->base.pc_next);
        break;
    case DISAS_JUMP:
        tcg_gen_lookup_and_goto_ptr();
        break;
    case DISAS_EXIT:
        tcg_gen_exit_tb(NULL, 0);
        break;
    case DISAS_NORETURN:
        break;
    default:
        g_assert_not_reached();
    }
}

static const TranslatorOps vc4_tr_ops = {
    .init_disas_context = vc4_tr_init_disas_context,
    .tb_start = vc4_tr_tb_start,
    .insn_start = vc4_tr_insn_start,
    .translate_insn = vc4_tr_translate_insn,
    .tb_stop = vc4_tr_tb_stop,
};

void vc4_translate_code(CPUState *cs, TranslationBlock *tb,
                        int *max_insns, vaddr pc, void *host_pc)
{
    DisasContext dc;

    translator_loop(cs, tb, max_insns, pc, host_pc, &vc4_tr_ops, &dc.base,
                    TCG_TYPE_VA);
}

void vc4_translate_init(void)
{
    static const char * const regnames[VC4_NUM_GPRS] = {
        "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
        "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
        "r16", "r17", "r18", "r19", "r20", "r21", "r22", "r23",
        "r24", "sp", "lr", "r27", "r28", "r29",
    };
    int i;

    for (i = 0; i < VC4_NUM_GPRS; i++) {
        cpu_gpr[i] = tcg_global_mem_new_i32(tcg_env,
                                             offsetof(CPUVC4State, gpr[i]),
                                             regnames[i]);
    }
    cpu_sr = tcg_global_mem_new_i32(tcg_env,
                                     offsetof(CPUVC4State, sr), "sr");
    cpu_pc = tcg_global_mem_new_i32(tcg_env,
                                     offsetof(CPUVC4State, pc), "pc");
}
