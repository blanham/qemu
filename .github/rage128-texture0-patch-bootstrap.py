#!/usr/bin/env python3

from pathlib import Path

patch = Path(".github/rage128-texture0-source-patch.py")
text = patch.read_text(encoding="utf-8")
old = '''def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one exact match, found {count}: {old[:80]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
'''
new = '''def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count == 1:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return

    old_lines = old.splitlines(keepends=True)
    text_lines = text.splitlines(keepends=True)
    matches = []
    for start in range(len(text_lines) - len(old_lines) + 1):
        window = text_lines[start:start + len(old_lines)]
        if all(actual.strip() == wanted.strip()
               for actual, wanted in zip(window, old_lines)):
            matches.append((start, start + len(old_lines)))

    if len(matches) != 1:
        raise SystemExit(
            f"{path}: expected one semantic match, found {len(matches)}: "
            f"{old[:80]!r}"
        )
    start_line, end_line = matches[0]
    start_offset = sum(len(line) for line in text_lines[:start_line])
    end_offset = sum(len(line) for line in text_lines[:end_line])
    path.write_text(text[:start_offset] + new + text[end_offset:],
                    encoding="utf-8")
'''
if text.count(old) != 1:
    raise SystemExit("source patch helper is not in the expected form")
patch.write_text(text.replace(old, new, 1), encoding="utf-8")

source = Path("hw/display/ati_3d.c")
text = source.read_text(encoding="utf-8")
start_marker = "    if (tex_control & (ATI_3D_TEXMAP_ENABLE |\n"
end_marker = "    if (!ati_3d_decode_surface(master, pitch_offset, &surface)) {\n"
replacement = '''    if (tex_control & (ATI_3D_SEC_TEXMAP_ENABLE |
                       ATI_3D_FOG_ENABLE |
                       ATI_3D_TEX_STENCIL_ENABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 secondary texture, fog, or stencil 3D is not implemented\\n");
        return false;
    }
    if (tex_control & ATI_3D_TEXMAP_ENABLE) {
        ATI3DTexture texture;

        if (!(format & ATI_3D_VERTEX_RHW) ||
            !(format & ATI_3D_VERTEX_ST)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "ATI Rage 128 textured vertices require RHW and ST\\n");
            return false;
        }
        if (!ati_3d_texture_decode(s, &texture)) {
            return false;
        }
    }
'''
if replacement not in text:
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise SystemExit("texture-enable markers are not unique")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    source.write_text(text[:start] + replacement + text[end:],
                      encoding="utf-8")
