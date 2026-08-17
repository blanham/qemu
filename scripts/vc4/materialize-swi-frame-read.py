#!/usr/bin/env python3
"""Replace monitor file export with direct VPU-address-space frame reads."""

from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    path = Path("scripts/vc4/check-swi-exception.py")
    text = path.read_text()

    text = replace_once(
        text,
        '        stack_path = tmp / "stack.bin"\n',
        "",
        "temporary stack-file declaration",
    )

    text = replace_once(
        text,
        '''            qmp.execute("stop")
            qmp.hmp(
                f"memsave 0x{EXCEPTION_STACK_TOP - 8:x} 8 {stack_path}",
                cpu_index=cpu_index,
            )
''',
        '''            qmp.execute("stop")
            frame = qmp.hmp(
                f"x /2wx 0x{EXCEPTION_STACK_TOP - 8:x}",
                cpu_index=cpu_index,
            )
''',
        "VPU frame read",
    )

    text = replace_once(
        text,
        '''        if not stack_path.is_file():
            raise RuntimeError("memsave did not create the stack image")
        saved_sr, saved_pc = struct.unpack("<II", stack_path.read_bytes())
''',
        '''        frame_words = re.findall(
            r"\\b0x([0-9a-fA-F]{8})\\b", frame
        )
        if len(frame_words) < 2:
            raise RuntimeError(
                "could not read the SWI frame through the VPU address space: "
                + frame
            )
        saved_sr, saved_pc = (int(word, 16) for word in frame_words[:2])
''',
        "VPU frame parser",
    )

    path.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
