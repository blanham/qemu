#!/usr/bin/env python3

from pathlib import Path

patch = Path(".github/rage128-texture0-patch.py")
code = patch.read_text(encoding="utf-8")

start_marker = (
    "\nreplace_once(\n"
    "    source,\n"
    "    \"\"\"    if (ctx->depth_enabled &&\n"
)
end_marker = (
    "\n)\n\nreplace_once(\n"
    "    source,\n"
    "    \"\"\"    if (!ati_3d_fragment_context_init"
)
start = code.index(start_marker)
end = code.index(end_marker, start) + len("\n)\n")
replacement = r'''
replace_regex_once(
    source,
    r"(    if \(ctx->depth_enabled &&\n"
    r"        !ati_3d_depth_surface\(s, &ctx->depth_surface, &ctx->depth_mask,\n"
    r"                              &ctx->depth_function\)\) \{\n"
    r".*?\n    \})\n    return true;",
    r"""\1
    ctx->texture_enabled = ctx->tex_control & ATI_3D_TEXMAP_ENABLE;
    if (ctx->texture_enabled && !ati_3d_texture_init(ctx)) {
        return false;
    }
    return true;""",
)
'''
code = code[:start] + replacement + code[end:]

start_marker = (
    "\nreplace_once(\n"
    "    source,\n"
    "    \"\"\"    if (tex_control & (ATI_3D_TEXMAP_ENABLE |\n"
)
end_marker = "\n)\n\ntext = source.read_text(encoding=\"utf-8\")"
start = code.index(start_marker)
end = code.index(end_marker, start) + len("\n)\n")
replacement = r'''
replace_regex_once(
    source,
    r"    if \(tex_control & \(ATI_3D_TEXMAP_ENABLE \|\n"
    r"                       ATI_3D_SEC_TEXMAP_ENABLE \|\n"
    r"                       ATI_3D_FOG_ENABLE \|\n"
    r"                       ATI_3D_TEX_STENCIL_ENABLE\)\) \{\n"
    r".*?\n    \}",
    r"""    if (tex_control & (ATI_3D_SEC_TEXMAP_ENABLE |
                       ATI_3D_FOG_ENABLE |
                       ATI_3D_TEX_STENCIL_ENABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 secondary texture, fog, or stencil 3D is not implemented\\n");
        return false;
    }
    if ((tex_control & ATI_3D_TEXMAP_ENABLE) &&
        !(format & ATI_3D_VERTEX_ST)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 textured draw is missing S/T coordinates\\n");
        return false;
    }""",
)
'''
code = code[:start] + replacement + code[end:]

old_texture_writer = r'''static void write_texture32(Rage128PM4Test *test, uint32_t offset,
                            const uint32_t *pixels, unsigned int count)
{
    uint32_t *raw = g_new(uint32_t, count);

    for (unsigned int i = 0; i < count; i++) {
        raw[i] = cpu_to_le32(pixels[i]);
    }
    qpci_memwrite(test->dev, test->framebuffer, offset, raw,
                  count * sizeof(*raw));
    g_free(raw);
}
'''
new_texture_writer = r'''static void write_texture32(Rage128PM4Test *test, uint32_t offset,
                            const uint32_t *pixels, unsigned int count)
{
    for (unsigned int i = 0; i < count; i++) {
        qpci_io_writel(test->dev, test->framebuffer,
                       offset + i * sizeof(uint32_t), pixels[i]);
    }
}
'''
if code.count(old_texture_writer) != 1:
    raise SystemExit("texture writer patch anchor is not unique")
code = code.replace(old_texture_writer, new_texture_writer, 1)

exec(compile(code, str(patch), "exec"))
