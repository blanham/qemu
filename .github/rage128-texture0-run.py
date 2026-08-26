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
    r'''\1
    ctx->texture_enabled = ctx->tex_control & ATI_3D_TEXMAP_ENABLE;
    if (ctx->texture_enabled && !ati_3d_texture_init(ctx)) {
        return false;
    }
    return true;''',
)
'''
code = code[:start] + replacement + code[end:]
exec(compile(code, str(patch), "exec"))
