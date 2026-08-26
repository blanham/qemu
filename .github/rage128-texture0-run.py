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
replacement = (
    "\nreplace_regex_once(\n"
    "    source,\n"
    "    r\"(    if \\(ctx->depth_enabled &&\\n\"\n"
    "    r\"        !ati_3d_depth_surface\\(s, &ctx->depth_surface, &ctx->depth_mask,\\n\"\n"
    "    r\"                              &ctx->depth_function\\)\\) \\{\\n\"\n"
    "    r\".*?\\n    \\})\\n    return true;\",\n"
    "    r\"\"\"\\1\n"
    "    ctx->texture_enabled = ctx->tex_control & ATI_3D_TEXMAP_ENABLE;\n"
    "    if (ctx->texture_enabled && !ati_3d_texture_init(ctx)) {\n"
    "        return false;\n"
    "    }\n"
    "    return true;\"\"\",\n"
    ")\n"
)
code = code[:start] + replacement + code[end:]
exec(compile(code, str(patch), "exec"))
