#!/usr/bin/env python3
"""Materialize the bounded VC4 shader-record and QPU frontier witness."""

from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: expected exactly one anchor, found {count}"
        )
    return text.replace(old, new, 1)


def patch_v3d() -> None:
    path = Path("hw/display/bcm2835_v3d.c")
    text = path.read_text(encoding="utf-8")
    if "vc4_v3d_frontier_report" in text:
        print(f"{path}: shader frontier already materialized")
        return

    text = replace_once(
        text,
        '#include "hw/display/bcm2835_v3d.h"\n',
        '#include "hw/display/bcm2835_v3d.h"\n'
        '#include "hw/display/vc4_v3d_frontier.h"\n',
        "frontier header include",
    )
    text = replace_once(
        text,
        """    uint8_t tile_x;
    uint8_t tile_y;
    bool have_render_config;
""",
        """    uint8_t tile_x;
    uint8_t tile_y;
    VC4V3DFrontierState frontier;
    bool have_render_config;
""",
        "control-list frontier state",
    )

    read_anchor = """static bool bcm2835_v3d_cl_read_u32(BCM2835V3DState *s, uint32_t address,
                                    uint32_t *value)
{
    uint8_t bytes[4];

    if (!bcm2835_v3d_cl_read(s, address, bytes, sizeof(bytes))) {
        return false;
    }
    *value = ldl_le_p(bytes);
    return true;
}

"""
    helper = """static bool bcm2835_v3d_frontier_read(void *opaque,
                                         uint32_t address,
                                         void *buffer, size_t size)
{
    BCM2835V3DState *s = opaque;

    return address_space_read(&s->dma_as, address,
                              MEMTXATTRS_UNSPECIFIED, buffer, size) ==
           MEMTX_OK;
}

static bool bcm2835_v3d_report_primitive_frontier(
    BCM2835V3DState *s, unsigned thread, VC4V3DCLState *cl,
    uint32_t pc, uint8_t packet)
{
    VC4V3DPrimitiveInfo primitive = {
        .pc = pc,
        .thread = thread,
        .packet = packet,
        .indexed = packet == VC4_PACKET_GL_INDEXED_PRIMITIVE,
    };

    if (!bcm2835_v3d_cl_read_u8(s, pc + 1,
                                &primitive.mode_byte) ||
        !bcm2835_v3d_cl_read_u32(s, pc + 2,
                                 &primitive.length)) {
        return true;
    }

    if (!primitive.indexed) {
        if (!bcm2835_v3d_cl_read_u32(s, pc + 6,
                                     &primitive.first)) {
            return true;
        }
    } else if (!bcm2835_v3d_cl_read_u32(
                   s, pc + 6, &primitive.index_address) ||
               !bcm2835_v3d_cl_read_u32(
                   s, pc + 10, &primitive.max_index)) {
        return true;
    }

    return vc4_v3d_frontier_report(
        bcm2835_v3d_frontier_read, s, TYPE_BCM2835_V3D,
        &cl->frontier, &primitive, &s->last_frontier_pc,
        &s->last_frontier_shader_record);
}

"""
    text = replace_once(
        text, read_anchor, read_anchor + helper,
        "frontier adapter insertion",
    )

    text = replace_once(
        text,
        """    case VC4_PACKET_WAIT_ON_SEMAPHORE:
    case VC4_PACKET_PRIMITIVE_LIST_FORMAT:
    case VC4_PACKET_GL_SHADER_STATE:
    case VC4_PACKET_NV_SHADER_STATE:
""",
        """    case VC4_PACKET_WAIT_ON_SEMAPHORE:
    case VC4_PACKET_PRIMITIVE_LIST_FORMAT:
    case VC4_PACKET_NV_SHADER_STATE:
""",
        "GL shader-state no-op removal",
    )
    text = replace_once(
        text,
        """    case VC4_PACKET_CLIPPER_Z_SCALING:
    case VC4_PACKET_TILE_BINNING_MODE_CONFIG:
    case VC4_PACKET_STORE_FULL_RES_TILE:
""",
        """    case VC4_PACKET_CLIPPER_Z_SCALING:
    case VC4_PACKET_STORE_FULL_RES_TILE:
""",
        "binning-config no-op removal",
    )

    text = replace_once(
        text,
        """    case VC4_PACKET_TILE_RENDERING_MODE_CONFIG:
        if (thread != 1 ||
""",
        """    case VC4_PACKET_TILE_BINNING_MODE_CONFIG:
        if (thread != 0 ||
            !bcm2835_v3d_cl_read_u32(
                s, pc + 1, &cl->frontier.bin_alloc_base) ||
            !bcm2835_v3d_cl_read_u32(
                s, pc + 5, &cl->frontier.bin_alloc_size) ||
            !bcm2835_v3d_cl_read_u32(
                s, pc + 9, &cl->frontier.bin_state_base) ||
            !bcm2835_v3d_cl_read_u8(
                s, pc + 13, &cl->frontier.bin_tiles_x) ||
            !bcm2835_v3d_cl_read_u8(
                s, pc + 14, &cl->frontier.bin_tiles_y) ||
            !bcm2835_v3d_cl_read_u8(
                s, pc + 15, &cl->frontier.bin_flags)) {
            return false;
        }
        cl->frontier.have_binning_config = true;
        return true;

    case VC4_PACKET_GL_SHADER_STATE:
        if (thread != 0 ||
            !bcm2835_v3d_cl_read_u32(
                s, pc + 1, &cl->frontier.shader_record)) {
            return false;
        }
        cl->frontier.have_shader_record = true;
        return true;

    case VC4_PACKET_TILE_RENDERING_MODE_CONFIG:
        if (thread != 1 ||
""",
        "frontier packet decoding",
    )

    text = replace_once(
        text,
        """    case VC4_PACKET_GL_INDEXED_PRIMITIVE:
    case VC4_PACKET_GL_ARRAY_PRIMITIVE:
    case VC4_PACKET_COMPRESSED_PRIMITIVE:
    case VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE:
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": packet 0x%02x requires binning/QPU execution "
                      "at 0x%08x\\n", packet, pc);
        return false;
""",
        """    case VC4_PACKET_GL_INDEXED_PRIMITIVE:
    case VC4_PACKET_GL_ARRAY_PRIMITIVE:
        if (bcm2835_v3d_report_primitive_frontier(
                s, thread, cl, pc, packet)) {
            qemu_log_mask(LOG_UNIMP,
                          TYPE_BCM2835_V3D
                          ": packet 0x%02x requires binning/QPU "
                          "execution at 0x%08x\\n", packet, pc);
        }
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        return false;

    case VC4_PACKET_COMPRESSED_PRIMITIVE:
    case VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE:
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": packet 0x%02x requires binning/QPU execution "
                      "at 0x%08x\\n", packet, pc);
        return false;
""",
        "primitive frontier",
    )

    text = replace_once(
        text,
        """    memset(s->regs, 0, sizeof(s->regs));
    bcm2835_v3d_update_irq(s);
""",
        """    memset(s->regs, 0, sizeof(s->regs));
    s->last_frontier_pc = UINT32_MAX;
    s->last_frontier_shader_record = UINT32_MAX;
    bcm2835_v3d_update_irq(s);
""",
        "frontier reset",
    )
    path.write_text(text, encoding="utf-8")


def patch_header() -> None:
    path = Path("include/hw/display/bcm2835_v3d.h")
    text = path.read_text(encoding="utf-8")
    if "last_frontier_shader_record" in text:
        print(f"{path}: shader frontier already materialized")
        return
    text = replace_once(
        text,
        """    uint32_t regs[BCM2835_V3D_REG_WORDS];
};
""",
        """    uint32_t regs[BCM2835_V3D_REG_WORDS];

    /* Diagnostic-only suppression for repeated kernel timeout retries. */
    uint32_t last_frontier_pc;
    uint32_t last_frontier_shader_record;
};
""",
        "frontier device state",
    )
    path.write_text(text, encoding="utf-8")


def patch_meson() -> None:
    path = Path("hw/display/meson.build")
    text = path.read_text(encoding="utf-8")
    if "vc4_v3d_frontier.c" in text:
        print(f"{path}: frontier sources already materialized")
        return
    text = replace_once(
        text,
        "system_ss.add(when: 'CONFIG_RASPI', if_true: "
        "files('bcm2835_fb.c', 'bcm2835_v3d.c', "
        "'bcm2835_hdmi.c', 'bcm2835_hvs.c', "
        "'bcm2835_pixelvalve.c'))",
        "system_ss.add(when: 'CONFIG_RASPI', if_true: "
        "files('bcm2835_fb.c', 'bcm2835_v3d.c', "
        "'vc4_qpu.c', 'vc4_v3d_frontier.c', "
        "'bcm2835_hdmi.c', 'bcm2835_hvs.c', "
        "'bcm2835_pixelvalve.c'))",
        "RASPI frontier sources",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_v3d()
    patch_header()
    patch_meson()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
