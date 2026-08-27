#!/usr/bin/env python3
"""Materialize bounded VC4 V3D control-list branch/sub-list execution."""

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
    if "VC4_PACKET_RETURN_FROM_SUB_LIST" in text:
        raise SystemExit(f"{V3D}: control-flow slice is already present")

    text = replace_once(
        text,
        "#define VC4_PACKET_BRANCH                    16\n"
        "#define VC4_PACKET_BRANCH_TO_SUB_LIST        17\n",
        "#define VC4_PACKET_BRANCH                    16\n"
        "#define VC4_PACKET_BRANCH_TO_SUB_LIST        17\n"
        "#define VC4_PACKET_RETURN_FROM_SUB_LIST      18\n",
        "packet constants",
    )
    text = replace_once(
        text,
        "#define VC4_MAX_CONTROL_LIST_BYTES (16 * MiB)\n"
        "#define VC4_MAX_CONTROL_LIST_STEPS (4 * 1024 * 1024)\n"
        "#define VC4_MAX_RENDER_DIMENSION   4096\n",
        "#define VC4_MAX_CONTROL_LIST_BYTES (16 * MiB)\n"
        "#define VC4_MAX_CONTROL_LIST_STEPS (4 * 1024 * 1024)\n"
        "#define VC4_MAX_SUB_LIST_DEPTH     2\n"
        "#define VC4_MAX_RENDER_DIMENSION   4096\n",
        "control-list limits",
    )
    text = replace_once(
        text,
        "    bool have_render_config;\n"
        "    bool have_clear_color;\n"
        "    bool saw_eof;\n"
        "} VC4V3DCLState;\n",
        "    bool have_render_config;\n"
        "    bool have_clear_color;\n"
        "    bool saw_eof;\n"
        "    uint32_t main_start;\n"
        "    uint32_t main_end;\n"
        "    uint32_t return_pc[VC4_MAX_SUB_LIST_DEPTH];\n"
        "    uint8_t sub_list_depth;\n"
        "} VC4V3DCLState;\n",
        "control-list state",
    )
    text = replace_once(
        text,
        "    case VC4_PACKET_WAIT_ON_SEMAPHORE:\n"
        "    case VC4_PACKET_STORE_MS_TILE_BUFFER:\n",
        "    case VC4_PACKET_WAIT_ON_SEMAPHORE:\n"
        "    case VC4_PACKET_RETURN_FROM_SUB_LIST:\n"
        "    case VC4_PACKET_STORE_MS_TILE_BUFFER:\n",
        "one-byte packet sizes",
    )

    old_execute = '''static bool bcm2835_v3d_execute_packet(BCM2835V3DState *s,
                                       unsigned thread,
                                       VC4V3DCLState *cl,
                                       uint32_t pc, uint32_t end,
                                       uint8_t packet, uint32_t *next_pc,
                                       bool *stop)
{
    unsigned size = bcm2835_v3d_packet_size(packet);

    if (size == 0 || end - pc < size) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_V3D
                      ": invalid/truncated packet 0x%02x at 0x%08x\\n",
                      packet, pc);
        return false;
    }

    *next_pc = pc + size;
    *stop = false;

    switch (packet) {
    case VC4_PACKET_HALT:
        *stop = true;
        return true;
    case VC4_PACKET_NOP:
    case VC4_PACKET_FLUSH:
    case VC4_PACKET_FLUSH_ALL:
    case VC4_PACKET_START_TILE_BINNING:
    case VC4_PACKET_INCREMENT_SEMAPHORE:
    case VC4_PACKET_WAIT_ON_SEMAPHORE:
    case VC4_PACKET_PRIMITIVE_LIST_FORMAT:
    case VC4_PACKET_GL_SHADER_STATE:
    case VC4_PACKET_NV_SHADER_STATE:
    case VC4_PACKET_VG_SHADER_STATE:
    case VC4_PACKET_CONFIGURATION_BITS:
    case VC4_PACKET_FLAT_SHADE_FLAGS:
    case VC4_PACKET_POINT_SIZE:
    case VC4_PACKET_LINE_WIDTH:
    case VC4_PACKET_RHT_X_BOUNDARY:
    case VC4_PACKET_DEPTH_OFFSET:
    case VC4_PACKET_CLIP_WINDOW:
    case VC4_PACKET_VIEWPORT_OFFSET:
    case VC4_PACKET_Z_CLIPPING:
    case VC4_PACKET_CLIPPER_XY_SCALING:
    case VC4_PACKET_CLIPPER_Z_SCALING:
    case VC4_PACKET_TILE_BINNING_MODE_CONFIG:
    case VC4_PACKET_STORE_FULL_RES_TILE:
    case VC4_PACKET_LOAD_FULL_RES_TILE:
    case VC4_PACKET_STORE_TILE_BUFFER_GENERAL:
    case VC4_PACKET_LOAD_TILE_BUFFER_GENERAL:
        return true;

    case VC4_PACKET_TILE_RENDERING_MODE_CONFIG:
        if (thread != 1 ||
            !bcm2835_v3d_cl_read_u32(s, pc + 1, &cl->render_base) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 5, &cl->width) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 7, &cl->height) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 9, &cl->render_config)) {
            return false;
        }
        cl->have_render_config = true;
        return true;

    case VC4_PACKET_CLEAR_COLORS:
        if (thread != 1 ||
            !bcm2835_v3d_cl_read_u32(s, pc + 1, &cl->clear_color[0]) ||
            !bcm2835_v3d_cl_read_u32(s, pc + 5, &cl->clear_color[1]) ||
            !bcm2835_v3d_cl_read_u32(s, pc + 9, &cl->clear_z) ||
            !bcm2835_v3d_cl_read_u8(s, pc + 13, &cl->clear_stencil)) {
            return false;
        }
        cl->have_clear_color = true;
        return true;

    case VC4_PACKET_TILE_COORDINATES:
        if (!bcm2835_v3d_cl_read_u8(s, pc + 1, &cl->tile_x) ||
            !bcm2835_v3d_cl_read_u8(s, pc + 2, &cl->tile_y)) {
            return false;
        }
        return true;

    case VC4_PACKET_STORE_MS_TILE_BUFFER:
    case VC4_PACKET_STORE_MS_TILE_BUFFER_EOF:
        if (thread != 1 || !bcm2835_v3d_store_clear_tile(s, cl)) {
            return false;
        }
        if (packet == VC4_PACKET_STORE_MS_TILE_BUFFER_EOF) {
            cl->saw_eof = true;
        }
        return true;

    case VC4_PACKET_BRANCH:
    case VC4_PACKET_BRANCH_TO_SUB_LIST:
    case VC4_PACKET_GL_INDEXED_PRIMITIVE:
    case VC4_PACKET_GL_ARRAY_PRIMITIVE:
    case VC4_PACKET_COMPRESSED_PRIMITIVE:
    case VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE:
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": packet 0x%02x requires binning/QPU execution "
                      "at 0x%08x\\n", packet, pc);
        return false;

    default:
        g_assert_not_reached();
    }
}
'''

    new_execute = '''static void bcm2835_v3d_update_sub_list_state(BCM2835V3DState *s,
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

static bool bcm2835_v3d_packet_fits(BCM2835V3DState *s,
                                    VC4V3DCLState *cl,
                                    uint32_t pc, uint8_t packet,
                                    unsigned size)
{
    bool wraps = size == 0 || UINT32_MAX - pc < size - 1;
    bool outside_main = cl->sub_list_depth == 0 &&
        (pc < cl->main_start || pc > cl->main_end ||
         cl->main_end - pc < size);

    if (!wraps && !outside_main) {
        return true;
    }

    s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
    qemu_log_mask(LOG_GUEST_ERROR,
                  TYPE_BCM2835_V3D
                  ": invalid/truncated packet 0x%02x at 0x%08x "
                  "(sub-list depth %u)\\n",
                  packet, pc, cl->sub_list_depth);
    return false;
}

static bool bcm2835_v3d_execute_packet(BCM2835V3DState *s,
                                       unsigned thread,
                                       VC4V3DCLState *cl,
                                       uint32_t pc,
                                       uint8_t packet, uint32_t *next_pc,
                                       bool *stop)
{
    unsigned size = bcm2835_v3d_packet_size(packet);
    uint32_t target;

    if (!bcm2835_v3d_packet_fits(s, cl, pc, packet, size)) {
        return false;
    }

    *next_pc = pc + size;
    *stop = false;

    switch (packet) {
    case VC4_PACKET_HALT:
        *stop = true;
        return true;
    case VC4_PACKET_NOP:
    case VC4_PACKET_FLUSH:
    case VC4_PACKET_FLUSH_ALL:
    case VC4_PACKET_START_TILE_BINNING:
    case VC4_PACKET_INCREMENT_SEMAPHORE:
    case VC4_PACKET_WAIT_ON_SEMAPHORE:
    case VC4_PACKET_PRIMITIVE_LIST_FORMAT:
    case VC4_PACKET_GL_SHADER_STATE:
    case VC4_PACKET_NV_SHADER_STATE:
    case VC4_PACKET_VG_SHADER_STATE:
    case VC4_PACKET_CONFIGURATION_BITS:
    case VC4_PACKET_FLAT_SHADE_FLAGS:
    case VC4_PACKET_POINT_SIZE:
    case VC4_PACKET_LINE_WIDTH:
    case VC4_PACKET_RHT_X_BOUNDARY:
    case VC4_PACKET_DEPTH_OFFSET:
    case VC4_PACKET_CLIP_WINDOW:
    case VC4_PACKET_VIEWPORT_OFFSET:
    case VC4_PACKET_Z_CLIPPING:
    case VC4_PACKET_CLIPPER_XY_SCALING:
    case VC4_PACKET_CLIPPER_Z_SCALING:
    case VC4_PACKET_TILE_BINNING_MODE_CONFIG:
    case VC4_PACKET_STORE_FULL_RES_TILE:
    case VC4_PACKET_LOAD_FULL_RES_TILE:
    case VC4_PACKET_STORE_TILE_BUFFER_GENERAL:
    case VC4_PACKET_LOAD_TILE_BUFFER_GENERAL:
        return true;

    case VC4_PACKET_BRANCH:
        if (!bcm2835_v3d_cl_read_u32(s, pc + 1, &target)) {
            return false;
        }
        if (cl->sub_list_depth == 0 &&
            (target < cl->main_start || target > cl->main_end)) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_V3D
                          ": main-list branch target 0x%08x outside "
                          "[0x%08x,0x%08x] at 0x%08x\\n",
                          target, cl->main_start, cl->main_end, pc);
            return false;
        }
        *next_pc = target;
        return true;

    case VC4_PACKET_BRANCH_TO_SUB_LIST:
        if (cl->sub_list_depth == VC4_MAX_SUB_LIST_DEPTH) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_V3D
                          ": sub-list nesting exceeds %u at 0x%08x\\n",
                          VC4_MAX_SUB_LIST_DEPTH, pc);
            return false;
        }
        if (!bcm2835_v3d_cl_read_u32(s, pc + 1, &target)) {
            return false;
        }
        cl->return_pc[cl->sub_list_depth++] = *next_pc;
        bcm2835_v3d_update_sub_list_state(s, thread, cl);
        *next_pc = target;
        return true;

    case VC4_PACKET_RETURN_FROM_SUB_LIST:
        if (cl->sub_list_depth == 0) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_V3D
                          ": return-from-sub-list without a caller "
                          "at 0x%08x\\n", pc);
            return false;
        }
        *next_pc = cl->return_pc[--cl->sub_list_depth];
        bcm2835_v3d_update_sub_list_state(s, thread, cl);
        return true;

    case VC4_PACKET_TILE_RENDERING_MODE_CONFIG:
        if (thread != 1 ||
            !bcm2835_v3d_cl_read_u32(s, pc + 1, &cl->render_base) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 5, &cl->width) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 7, &cl->height) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 9, &cl->render_config)) {
            return false;
        }
        cl->have_render_config = true;
        return true;

    case VC4_PACKET_CLEAR_COLORS:
        if (thread != 1 ||
            !bcm2835_v3d_cl_read_u32(s, pc + 1, &cl->clear_color[0]) ||
            !bcm2835_v3d_cl_read_u32(s, pc + 5, &cl->clear_color[1]) ||
            !bcm2835_v3d_cl_read_u32(s, pc + 9, &cl->clear_z) ||
            !bcm2835_v3d_cl_read_u8(s, pc + 13, &cl->clear_stencil)) {
            return false;
        }
        cl->have_clear_color = true;
        return true;

    case VC4_PACKET_TILE_COORDINATES:
        if (!bcm2835_v3d_cl_read_u8(s, pc + 1, &cl->tile_x) ||
            !bcm2835_v3d_cl_read_u8(s, pc + 2, &cl->tile_y)) {
            return false;
        }
        return true;

    case VC4_PACKET_STORE_MS_TILE_BUFFER:
    case VC4_PACKET_STORE_MS_TILE_BUFFER_EOF:
        if (thread != 1 || !bcm2835_v3d_store_clear_tile(s, cl)) {
            return false;
        }
        if (packet == VC4_PACKET_STORE_MS_TILE_BUFFER_EOF) {
            cl->saw_eof = true;
        }
        return true;

    case VC4_PACKET_GL_INDEXED_PRIMITIVE:
    case VC4_PACKET_GL_ARRAY_PRIMITIVE:
    case VC4_PACKET_COMPRESSED_PRIMITIVE:
    case VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE:
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": packet 0x%02x requires binning/QPU execution "
                      "at 0x%08x\\n", packet, pc);
        return false;

    default:
        g_assert_not_reached();
    }
}
'''
    text = replace_once(text, old_execute, new_execute, "packet executor")

    text = replace_once(
        text,
        "    s->regs[cs_index] &= ~V3D_CTERR;\n"
        "    s->regs[REG_INDEX(V3D_CTNCA(thread))] =\n"
        "        s->regs[REG_INDEX(V3D_CTNEA(thread))];\n",
        "    s->regs[cs_index] &= ~(V3D_CTERR | V3D_CTSUBS);\n"
        "    s->regs[REG_INDEX(V3D_CT00RA0 + thread * 4)] = 0;\n"
        "    s->regs[REG_INDEX(V3D_CT0LC + thread * 4)] = 0;\n"
        "    s->regs[REG_INDEX(V3D_CTNCA(thread))] =\n"
        "        s->regs[REG_INDEX(V3D_CTNEA(thread))];\n",
        "successful thread completion",
    )

    old_loop = '''    while (pc < end && steps++ < VC4_MAX_CONTROL_LIST_STEPS) {
        uint8_t packet;

        s->regs[REG_INDEX(V3D_CTNCA(thread))] = pc;
        if (!bcm2835_v3d_cl_read_u8(s, pc, &packet) ||
            !bcm2835_v3d_execute_packet(s, thread, &cl, pc, end,
                                        packet, &next_pc, &stop)) {
            success = false;
            break;
        }
        pc = next_pc;
        if (stop) {
            break;
        }
    }

    if (steps >= VC4_MAX_CONTROL_LIST_STEPS) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
        success = false;
    }
'''
    new_loop = '''    cl.main_start = pc;
    cl.main_end = end;

    while (steps < VC4_MAX_CONTROL_LIST_STEPS) {
        uint8_t packet;

        if (cl.sub_list_depth == 0 && pc == end) {
            stop = true;
            break;
        }
        if (cl.sub_list_depth == 0 &&
            (pc < cl.main_start || pc > cl.main_end)) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            success = false;
            break;
        }

        steps++;
        s->regs[REG_INDEX(V3D_CTNCA(thread))] = pc;
        if (!bcm2835_v3d_cl_read_u8(s, pc, &packet) ||
            !bcm2835_v3d_execute_packet(s, thread, &cl, pc,
                                        packet, &next_pc, &stop)) {
            success = false;
            break;
        }
        pc = next_pc;
        if (stop) {
            break;
        }
    }

    if (success && !stop) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_V3D
                      ": control-list step limit exceeded at 0x%08x\\n",
                      pc);
        success = false;
    }
'''
    text = replace_once(text, old_loop, new_loop,
                        "control-list execution loop")
    V3D.write_text(text, encoding="utf-8")


def patch_smoke() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    if "VC4_PACKET_RETURN_FROM_SUB_LIST" in text:
        raise SystemExit(f"{SMOKE}: control-flow tests are already present")

    text = replace_once(
        text,
        "V3D_CT1EA = V3D_BASE + 0x10C\n"
        "V3D_CT1CA = V3D_BASE + 0x114\n"
        "V3D_RFC = V3D_BASE + 0x138\n",
        "V3D_CT1EA = V3D_BASE + 0x10C\n"
        "V3D_CT1CA = V3D_BASE + 0x114\n"
        "V3D_CT1RA0 = V3D_BASE + 0x11C\n"
        "V3D_CT1LC = V3D_BASE + 0x124\n"
        "V3D_RFC = V3D_BASE + 0x138\n",
        "control-thread registers",
    )
    text = replace_once(
        text,
        "V3D_CTRSTA = 1 << 15\n"
        "V3D_CTERR = 1 << 3\n",
        "V3D_CTRSTA = 1 << 15\n"
        "V3D_CTSUBS = 1 << 4\n"
        "V3D_CTERR = 1 << 3\n",
        "control-thread status bits",
    )
    text = replace_once(
        text,
        "VC4_PACKET_HALT = 0\n"
        "VC4_PACKET_STORE_MS_TILE_BUFFER_EOF = 25\n",
        "VC4_PACKET_HALT = 0\n"
        "VC4_PACKET_BRANCH = 16\n"
        "VC4_PACKET_BRANCH_TO_SUB_LIST = 17\n"
        "VC4_PACKET_RETURN_FROM_SUB_LIST = 18\n"
        "VC4_PACKET_STORE_MS_TILE_BUFFER_EOF = 25\n",
        "control-flow packet constants",
    )
    text = replace_once(
        text,
        "CL_ADDRESS = 0x00080000\n"
        "BAD_CL_ADDRESS = 0x00081000\n"
        "FRAMEBUFFER_ADDRESS = 0x00100000\n",
        "CL_ADDRESS = 0x00080000\n"
        "BAD_CL_ADDRESS = 0x00081000\n"
        "SUB_LIST_ADDRESS = 0x00082000\n"
        "CONTROL_FLOW_CL_ADDRESS = 0x00083000\n"
        "BRANCH_CL_ADDRESS = 0x00084000\n"
        "RETURN_ERROR_CL_ADDRESS = 0x00085000\n"
        "NESTED_MAIN_ADDRESS = 0x00086000\n"
        "NESTED_LEVEL_1_ADDRESS = 0x00087000\n"
        "NESTED_LEVEL_2_ADDRESS = 0x00088000\n"
        "FRAMEBUFFER_ADDRESS = 0x00100000\n",
        "control-list addresses",
    )

    old_builder = '''def build_unsupported_rcl() -> bytes:
    # Primitive execution is intentionally rejected until binning/QPU support
    # exists.  The test prevents a future regression into fake completions.
    return bytes((VC4_PACKET_GL_ARRAY_PRIMITIVE,)) + bytes(9)
'''
    new_builder = '''def build_control_flow_rcl() -> tuple[bytes, bytes]:
    main = bytearray()
    main.append(VC4_PACKET_TILE_RENDERING_MODE_CONFIG)
    main += struct.pack(
        "<IHHH",
        FRAMEBUFFER_ADDRESS,
        WIDTH,
        HEIGHT,
        VC4_RENDER_CONFIG_FORMAT_RGBA8888,
    )
    main.append(VC4_PACKET_CLEAR_COLORS)
    main += struct.pack("<III", CLEAR_COLOR, CLEAR_COLOR, 0x00FFFFFF)
    main.append(0)
    main.append(VC4_PACKET_BRANCH_TO_SUB_LIST)
    main += struct.pack("<I", SUB_LIST_ADDRESS)
    main.append(VC4_PACKET_HALT)

    sub_list = bytearray((VC4_PACKET_TILE_COORDINATES, 0, 0))
    sub_list.append(VC4_PACKET_STORE_MS_TILE_BUFFER_EOF)
    sub_list.append(VC4_PACKET_RETURN_FROM_SUB_LIST)
    return bytes(main), bytes(sub_list)


def build_branch_rcl(address: int) -> bytes:
    skipped = bytes((VC4_PACKET_GL_ARRAY_PRIMITIVE,)) + bytes(9)
    target = address + 5 + len(skipped)
    return (
        bytes((VC4_PACKET_BRANCH,)) + struct.pack("<I", target) +
        skipped + bytes((VC4_PACKET_HALT,))
    )


def build_nested_sub_lists() -> tuple[bytes, bytes, bytes]:
    main = (
        bytes((VC4_PACKET_BRANCH_TO_SUB_LIST,)) +
        struct.pack("<I", NESTED_LEVEL_1_ADDRESS) +
        bytes((VC4_PACKET_HALT,))
    )
    level_1 = (
        bytes((VC4_PACKET_BRANCH_TO_SUB_LIST,)) +
        struct.pack("<I", NESTED_LEVEL_2_ADDRESS) +
        bytes((VC4_PACKET_RETURN_FROM_SUB_LIST,))
    )
    level_2 = (
        bytes((VC4_PACKET_BRANCH_TO_SUB_LIST,)) +
        struct.pack("<I", SUB_LIST_ADDRESS) +
        bytes((VC4_PACKET_RETURN_FROM_SUB_LIST,))
    )
    return main, level_1, level_2


def build_unsupported_rcl() -> bytes:
    # Primitive execution is intentionally rejected until binning/QPU support
    # exists.  The test prevents a future regression into fake completions.
    return bytes((VC4_PACKET_GL_ARRAY_PRIMITIVE,)) + bytes(9)
'''
    text = replace_once(text, old_builder, new_builder, "test list builders")

    anchor = '''    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)
    expect("render-done acknowledgement", qtest.readl(V3D_INTCTL), 0)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    bad_rcl = build_unsupported_rcl()
'''
    replacement = '''    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)
    expect("render-done acknowledgement", qtest.readl(V3D_INTCTL), 0)

    main_rcl, sub_list = build_control_flow_rcl()
    qtest.write_blob(CONTROL_FLOW_CL_ADDRESS, main_rcl)
    qtest.write_blob(SUB_LIST_ADDRESS, sub_list)
    qtest.writel(FRAMEBUFFER_ADDRESS, 0)
    qtest.writel(FRAMEBUFFER_ADDRESS + (WIDTH * HEIGHT - 1) * 4, 0)
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT1CA, CONTROL_FLOW_CL_ADDRESS)
    qtest.writel(V3D_CT1EA, CONTROL_FLOW_CL_ADDRESS + len(main_rcl))
    expect("sub-list render frame count", qtest.readl(V3D_RFC), 2)
    expect("sub-list thread status", qtest.readl(V3D_CT1CS), 0)
    expect("sub-list return-address cleanup", qtest.readl(V3D_CT1RA0), 0)
    expect("sub-list counter cleanup", qtest.readl(V3D_CT1LC), 0)
    expect("sub-list top-left clear", qtest.readl(FRAMEBUFFER_ADDRESS),
           CLEAR_COLOR)
    expect("sub-list bottom-right clear",
           qtest.readl(FRAMEBUFFER_ADDRESS + (WIDTH * HEIGHT - 1) * 4),
           CLEAR_COLOR)
    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)

    branch_rcl = build_branch_rcl(BRANCH_CL_ADDRESS)
    qtest.write_blob(BRANCH_CL_ADDRESS, branch_rcl)
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT1CA, BRANCH_CL_ADDRESS)
    qtest.writel(V3D_CT1EA, BRANCH_CL_ADDRESS + len(branch_rcl))
    expect("bounded branch frame count", qtest.readl(V3D_RFC), 3)
    expect("bounded branch status", qtest.readl(V3D_CT1CS), 0)
    expect("bounded branch ERRSTAT", qtest.readl(V3D_ERRSTAT), 0)
    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    bad_rcl = build_unsupported_rcl()
'''
    text = replace_once(text, anchor, replacement, "control-flow exercises")

    text = replace_once(
        text,
        '''    expect("failed render did not increment frame count",
           qtest.readl(V3D_RFC), 1)
''',
        '''    expect("failed render did not increment frame count",
           qtest.readl(V3D_RFC), 3)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    unmatched_return = bytes((VC4_PACKET_RETURN_FROM_SUB_LIST,))
    qtest.write_blob(RETURN_ERROR_CL_ADDRESS, unmatched_return)
    qtest.writel(V3D_CT1CA, RETURN_ERROR_CL_ADDRESS)
    qtest.writel(V3D_CT1EA,
                 RETURN_ERROR_CL_ADDRESS + len(unmatched_return))
    if not (qtest.readl(V3D_CT1CS) & V3D_CTERR):
        raise RuntimeError("unmatched sub-list return did not set CTERR")
    if qtest.readl(V3D_ERRSTAT) == 0:
        raise RuntimeError("unmatched sub-list return did not set ERRSTAT")
    expect("unmatched return frame count", qtest.readl(V3D_RFC), 3)

    nested_main, nested_1, nested_2 = build_nested_sub_lists()
    qtest.write_blob(NESTED_MAIN_ADDRESS, nested_main)
    qtest.write_blob(NESTED_LEVEL_1_ADDRESS, nested_1)
    qtest.write_blob(NESTED_LEVEL_2_ADDRESS, nested_2)
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT1CA, NESTED_MAIN_ADDRESS)
    qtest.writel(V3D_CT1EA, NESTED_MAIN_ADDRESS + len(nested_main))
    nested_status = qtest.readl(V3D_CT1CS)
    if not (nested_status & V3D_CTERR):
        raise RuntimeError("excessive sub-list nesting did not set CTERR")
    if not (nested_status & V3D_CTSUBS):
        raise RuntimeError("failed nested sub-list lost CTSUBS state")
    expect("failed nested sub-list depth", qtest.readl(V3D_CT1LC), 2)
    if qtest.readl(V3D_CT1RA0) == 0:
        raise RuntimeError("failed nested sub-list lost return address")
    if qtest.readl(V3D_ERRSTAT) == 0:
        raise RuntimeError("excessive sub-list nesting did not set ERRSTAT")
    expect("nested sub-list frame count", qtest.readl(V3D_RFC), 3)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    expect("nested reset thread status", qtest.readl(V3D_CT1CS), 0)
    expect("nested reset return address", qtest.readl(V3D_CT1RA0), 0)
    expect("nested reset list counter", qtest.readl(V3D_CT1LC), 0)
''',
        "failure tests",
    )
    text = replace_once(
        text,
        'print("BCM2835 V3D register and clear-render smoke test passed")',
        'print("BCM2835 V3D register, clear-render, and control-flow smoke test passed")',
        "success message",
    )
    SMOKE.write_text(text, encoding="utf-8")


def main() -> int:
    patch_v3d()
    patch_smoke()
    print("Materialized bounded BCM2835 V3D control-list execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
