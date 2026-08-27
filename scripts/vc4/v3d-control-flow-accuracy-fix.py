#!/usr/bin/env python3
"""Correct VC4 control-thread status and list-counter semantics."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V3D = ROOT / "hw/display/bcm2835_v3d.c"
SMOKE = ROOT / "scripts/vc4/v3d-smoke.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_v3d() -> None:
    text = V3D.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "#define V3D_CTRSTA             (1u << 15)\n"
        "#define V3D_CTSEMA             (1u << 12)\n"
        "#define V3D_CTRTSD             (1u << 8)\n"
        "#define V3D_CTRUN              (1u << 5)\n",
        "#define V3D_CTRSTA             (1u << 15)\n"
        "#define V3D_CTSEMA_SHIFT       12\n"
        "#define V3D_CTSEMA_MASK        (0x7u << V3D_CTSEMA_SHIFT)\n"
        "#define V3D_CTRTSD_SHIFT       8\n"
        "#define V3D_CTRTSD_MASK        (0x3u << V3D_CTRTSD_SHIFT)\n"
        "#define V3D_CTRUN              (1u << 5)\n",
        "control-thread fields",
    )

    old_helper = '''static void bcm2835_v3d_update_sub_list_state(BCM2835V3DState *s,
                                                  unsigned thread,
                                                  VC4V3DCLState *cl)
{
    uint32_t cs_index = REG_INDEX(V3D_CTNCS(thread));
    uint32_t ra_index = REG_INDEX(V3D_CT00RA0 + thread * 4);
    uint32_t lc_index = REG_INDEX(V3D_CT0LC + thread * 4);

    if (cl->sub_list_depth != 0) {
        s->regs[cs_index] |= V3D_CTSUBS;
        s->regs[ra_index] = cl->return_pc[cl->sub_list_depth - 1];
    } else {
        s->regs[cs_index] &= ~V3D_CTSUBS;
        s->regs[ra_index] = 0;
    }
    s->regs[lc_index] = cl->sub_list_depth;
}
'''
    new_helper = '''static void bcm2835_v3d_update_sub_list_state(BCM2835V3DState *s,
                                                  unsigned thread,
                                                  VC4V3DCLState *cl)
{
    uint32_t cs_index = REG_INDEX(V3D_CTNCS(thread));
    uint32_t ra_index = REG_INDEX(V3D_CT00RA0 + thread * 4);

    s->regs[cs_index] =
        (s->regs[cs_index] & ~V3D_CTRTSD_MASK) |
        ((uint32_t)cl->sub_list_depth << V3D_CTRTSD_SHIFT);
    if (cl->sub_list_depth != 0) {
        s->regs[ra_index] = cl->return_pc[cl->sub_list_depth - 1];
    } else {
        s->regs[ra_index] = 0;
    }
}

static void bcm2835_v3d_increment_list_counter(BCM2835V3DState *s,
                                                unsigned thread,
                                                bool major)
{
    uint32_t index = REG_INDEX(V3D_CT0LC + thread * 4);
    unsigned shift = major ? 16 : 0;
    uint32_t mask = 0xffffu << shift;
    uint32_t count = ((s->regs[index] & mask) >> shift) + 1;

    s->regs[index] = (s->regs[index] & ~mask) |
                     ((count & 0xffffu) << shift);
}
'''
    text = replace_once(text, old_helper, new_helper,
                        "sub-list status helper")

    text = replace_once(
        text,
        "    case VC4_PACKET_NOP:\n"
        "    case VC4_PACKET_FLUSH:\n"
        "    case VC4_PACKET_FLUSH_ALL:\n"
        "    case VC4_PACKET_START_TILE_BINNING:\n",
        "    case VC4_PACKET_NOP:\n"
        "    case VC4_PACKET_START_TILE_BINNING:\n",
        "flush no-op removal",
    )
    text = replace_once(
        text,
        "    case VC4_PACKET_BRANCH:\n"
        "        if (!bcm2835_v3d_cl_read_u32(s, pc + 1, &target)) {\n",
        "    case VC4_PACKET_FLUSH:\n"
        "    case VC4_PACKET_FLUSH_ALL:\n"
        "        bcm2835_v3d_increment_list_counter(s, thread, true);\n"
        "        return true;\n\n"
        "    case VC4_PACKET_BRANCH:\n"
        "        if (!bcm2835_v3d_cl_read_u32(s, pc + 1, &target)) {\n",
        "flush counter execution",
    )
    text = replace_once(
        text,
        "        *next_pc = cl->return_pc[--cl->sub_list_depth];\n"
        "        bcm2835_v3d_update_sub_list_state(s, thread, cl);\n"
        "        return true;\n",
        "        *next_pc = cl->return_pc[--cl->sub_list_depth];\n"
        "        bcm2835_v3d_update_sub_list_state(s, thread, cl);\n"
        "        bcm2835_v3d_increment_list_counter(s, thread, false);\n"
        "        return true;\n",
        "return counter execution",
    )

    text = replace_once(
        text,
        "static void bcm2835_v3d_complete_thread(BCM2835V3DState *s,\n"
        "                                         unsigned thread, bool success)\n",
        "static void bcm2835_v3d_complete_thread(BCM2835V3DState *s,\n"
        "                                         unsigned thread, bool success,\n"
        "                                         bool halted)\n",
        "thread completion signature",
    )
    text = replace_once(
        text,
        "    s->regs[cs_index] &= ~(V3D_CTERR | V3D_CTSUBS);\n"
        "    s->regs[REG_INDEX(V3D_CT00RA0 + thread * 4)] = 0;\n"
        "    s->regs[REG_INDEX(V3D_CT0LC + thread * 4)] = 0;\n"
        "    s->regs[REG_INDEX(V3D_CTNCA(thread))] =\n",
        "    s->regs[cs_index] &= ~(V3D_CTERR | V3D_CTSUBS |\n"
        "                           V3D_CTRTSD_MASK);\n"
        "    if (halted) {\n"
        "        s->regs[cs_index] |= V3D_CTSUBS;\n"
        "    }\n"
        "    s->regs[REG_INDEX(V3D_CT00RA0 + thread * 4)] = 0;\n"
        "    s->regs[REG_INDEX(V3D_CTNCA(thread))] =\n",
        "thread completion state",
    )
    text = replace_once(
        text,
        "        bcm2835_v3d_complete_thread(s, thread, false);\n",
        "        bcm2835_v3d_complete_thread(s, thread, false, false);\n",
        "invalid-range completion",
    )
    text = replace_once(
        text,
        "    bool stop;\n"
        "    bool success = true;\n",
        "    bool stop;\n"
        "    bool halted = false;\n"
        "    bool finished = false;\n"
        "    bool success = true;\n",
        "thread execution flags",
    )
    text = replace_once(
        text,
        "        if (cl.sub_list_depth == 0 && pc == end) {\n"
        "            stop = true;\n"
        "            break;\n"
        "        }\n",
        "        if (cl.sub_list_depth == 0 && pc == end) {\n"
        "            finished = true;\n"
        "            break;\n"
        "        }\n",
        "natural list completion",
    )
    text = replace_once(
        text,
        "        if (stop) {\n"
        "            break;\n"
        "        }\n"
        "    }\n\n"
        "    if (success && !stop) {\n",
        "        if (stop) {\n"
        "            halted = true;\n"
        "            finished = true;\n"
        "            break;\n"
        "        }\n"
        "    }\n\n"
        "    if (success && !finished) {\n",
        "halt and step-limit state",
    )
    text = replace_once(
        text,
        "    bcm2835_v3d_complete_thread(s, thread, success);\n",
        "    bcm2835_v3d_complete_thread(s, thread, success, halted);\n",
        "final thread completion",
    )

    text = replace_once(
        text,
        "        s->regs[index] = (s->regs[index] & (V3D_CTRUN | V3D_CTERR)) |\n"
        "                         (v & (V3D_CTSEMA | V3D_CTRTSD |\n"
        "                               V3D_CTSUBS | V3D_CTMODE));\n",
        "        s->regs[index] =\n"
        "            (s->regs[index] & (V3D_CTRUN | V3D_CTERR |\n"
        "                               V3D_CTSEMA_MASK | V3D_CTRTSD_MASK |\n"
        "                               V3D_CTMODE)) |\n"
        "            (v & V3D_CTSUBS);\n",
        "control-thread write mask",
    )
    text = replace_once(
        text,
        "    case V3D_BFC:\n"
        "    case V3D_RFC:\n",
        "    case V3D_CT0LC:\n"
        "    case V3D_CT1LC:\n"
        "        if (v & 1u) {\n"
        "            s->regs[index] &= 0xffff0000u;\n"
        "        }\n"
        "        if (v & (1u << 16)) {\n"
        "            s->regs[index] &= 0x0000ffffu;\n"
        "        }\n"
        "        return;\n"
        "    case V3D_BFC:\n"
        "    case V3D_RFC:\n",
        "list-counter reset writes",
    )
    V3D.write_text(text, encoding="utf-8")


def patch_smoke() -> None:
    text = SMOKE.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "V3D_CTRSTA = 1 << 15\n"
        "V3D_CTSUBS = 1 << 4\n"
        "V3D_CTERR = 1 << 3\n",
        "V3D_CTRSTA = 1 << 15\n"
        "V3D_CTRTSD_SHIFT = 8\n"
        "V3D_CTRTSD_MASK = 3 << V3D_CTRTSD_SHIFT\n"
        "V3D_CTSUBS = 1 << 4\n"
        "V3D_CTERR = 1 << 3\n",
        "test control-thread fields",
    )
    text = replace_once(
        text,
        "VC4_PACKET_HALT = 0\n"
        "VC4_PACKET_BRANCH = 16\n",
        "VC4_PACKET_HALT = 0\n"
        "VC4_PACKET_FLUSH = 4\n"
        "VC4_PACKET_BRANCH = 16\n",
        "test flush packet",
    )
    text = replace_once(
        text,
        "        skipped + bytes((VC4_PACKET_HALT,))\n",
        "        skipped + bytes((VC4_PACKET_FLUSH, VC4_PACKET_HALT))\n",
        "branch-list flush",
    )
    text = replace_once(
        text,
        "    expect(\"render thread status\", qtest.readl(V3D_CT1CS), 0)\n",
        "    expect(\"render thread halt status\", qtest.readl(V3D_CT1CS),\n"
        "           V3D_CTSUBS)\n",
        "initial halt status",
    )
    text = replace_once(
        text,
        "    expect(\"sub-list thread status\", qtest.readl(V3D_CT1CS), 0)\n"
        "    expect(\"sub-list return-address cleanup\", qtest.readl(V3D_CT1RA0), 0)\n"
        "    expect(\"sub-list counter cleanup\", qtest.readl(V3D_CT1LC), 0)\n",
        "    expect(\"sub-list thread halt status\", qtest.readl(V3D_CT1CS),\n"
        "           V3D_CTSUBS)\n"
        "    expect(\"sub-list return-address cleanup\", qtest.readl(V3D_CT1RA0), 0)\n"
        "    expect(\"sub-list return count\", qtest.readl(V3D_CT1LC), 1)\n"
        "    qtest.writel(V3D_CT1LC, 1)\n"
        "    expect(\"sub-list counter reset\", qtest.readl(V3D_CT1LC), 0)\n",
        "successful sub-list state",
    )
    text = replace_once(
        text,
        "    expect(\"bounded branch status\", qtest.readl(V3D_CT1CS), 0)\n"
        "    expect(\"bounded branch ERRSTAT\", qtest.readl(V3D_ERRSTAT), 0)\n",
        "    expect(\"bounded branch halt status\", qtest.readl(V3D_CT1CS),\n"
        "           V3D_CTSUBS)\n"
        "    expect(\"bounded branch flush count\", qtest.readl(V3D_CT1LC),\n"
        "           1 << 16)\n"
        "    qtest.writel(V3D_CT1LC, 1 << 16)\n"
        "    expect(\"bounded branch counter reset\", qtest.readl(V3D_CT1LC), 0)\n"
        "    expect(\"bounded branch ERRSTAT\", qtest.readl(V3D_ERRSTAT), 0)\n",
        "successful branch state",
    )
    text = replace_once(
        text,
        "    if not (nested_status & V3D_CTSUBS):\n"
        "        raise RuntimeError(\"failed nested sub-list lost CTSUBS state\")\n"
        "    expect(\"failed nested sub-list depth\", qtest.readl(V3D_CT1LC), 2)\n",
        "    expect(\"failed nested sub-list depth\",\n"
        "           (nested_status & V3D_CTRTSD_MASK) >> V3D_CTRTSD_SHIFT, 2)\n"
        "    expect(\"failed nested sub-list return count\",\n"
        "           qtest.readl(V3D_CT1LC), 0)\n",
        "failed nested sub-list state",
    )
    SMOKE.write_text(text, encoding="utf-8")


def main() -> int:
    patch_v3d()
    patch_smoke()
    print("Corrected BCM2835 V3D control-thread register semantics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
