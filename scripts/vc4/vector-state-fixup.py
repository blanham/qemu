#!/usr/bin/env python3
"""Add a one-shot stock-firmware interrupt/vector snapshot to the state probe.

The generated diagnostic is deliberately kept out of production target code.
It records the exact interrupt-controller state, vector word, exception frame,
low boot-cache contents, and the 0x60000000 boot-ROM aperture when the VPU
settles at the current low-PC frontier.
"""

from __future__ import annotations

from pathlib import Path


PROBE = Path("scripts/vc4/raspi3-stock-bootcode-0200.py")
MARKER = "STOCK_VECTOR_STATE"
ANCHOR = "            progress = analyze_snapshots(snapshots)\n"

INSERTION = r'''

            def read_words(address: int, count: int) -> dict[str, object]:
                raw = state.flatten(
                    self.hmp(
                        f"x /{count}wx 0x{address:x}",
                        cpu_index=vpu_index,
                    )
                )
                values = [
                    int(value, 16)
                    for value in re.findall(
                        r"0x([0-9a-fA-F]{8})", raw
                    )
                ]
                return {
                    "address": f"0x{address:08x}",
                    "values": [f"0x{value:08x}" for value in values],
                    "raw": raw,
                }

            def read_bytes(address: int, count: int) -> dict[str, object]:
                raw = state.flatten(
                    self.hmp(
                        f"x /{count}bx 0x{address:x}",
                        cpu_index=vpu_index,
                    )
                )
                values = [
                    int(value, 16)
                    for value in re.findall(
                        r"0x([0-9a-fA-F]{2})", raw
                    )
                ]
                return {
                    "address": f"0x{address:08x}",
                    "values": [f"0x{value:02x}" for value in values],
                    "raw": raw,
                }

            intc0 = read_words(0x7e002000, 24)
            intc1 = read_words(0x7e002800, 24)
            intc0_values = [
                int(value, 16)
                for value in intc0["values"]
            ]
            status = intc0_values[1] if len(intc0_values) > 1 else 0
            vector_base = (
                intc0_values[12] if len(intc0_values) > 12 else 0
            )
            vector = status & 0x7f
            priority = (status >> 8) & 7
            vector_address = (
                vector_base + vector * 4
            ) & 0xffffffff
            vector_word_dump = read_words(vector_address, 1)
            vector_words = [
                int(value, 16)
                for value in vector_word_dump["values"]
            ]
            vector_word = vector_words[0] if vector_words else 0
            vector_target = vector_word & ~1
            stack_pointer = final_registers.get(25, 0)
            low_words = read_words(0, 128)
            low_values = [
                int(value, 16)
                for value in low_words["values"]
            ]
            vector_state = {
                "pc": f"0x{snapshots[-1]['pc']:08x}",
                "sr": f"0x{snapshots[-1]['sr']:08x}",
                "lr": f"0x{final_registers.get(26, 0):08x}",
                "sp": f"0x{stack_pointer:08x}",
                "intc0": intc0,
                "intc1": intc1,
                "control": (
                    f"0x{intc0_values[0]:08x}"
                    if intc0_values else None
                ),
                "status": f"0x{status:08x}",
                "source0": (
                    f"0x{intc0_values[2]:08x}"
                    if len(intc0_values) > 2 else None
                ),
                "source1": (
                    f"0x{intc0_values[3]:08x}"
                    if len(intc0_values) > 3 else None
                ),
                "masks": [
                    f"0x{value:08x}"
                    for value in intc0_values[4:12]
                ],
                "active_vector": vector,
                "active_priority": priority,
                "vector_base": f"0x{vector_base:08x}",
                "vector_address": f"0x{vector_address:08x}",
                "vector_word": f"0x{vector_word:08x}",
                "vector_scalar_mode": bool(vector_word & 1),
                "vector_target": f"0x{vector_target:08x}",
                "vector_word_dump": vector_word_dump,
                "vector_target_bytes": read_bytes(vector_target, 32),
                "exception_frame": read_words(stack_pointer, 8),
                "low_boot_cache": low_words,
                "low_nonzero_words": [
                    {
                        "address": f"0x{index * 4:08x}",
                        "value": f"0x{value:08x}",
                    }
                    for index, value in enumerate(low_values)
                    if value != 0
                ],
                "boot_rom_aperture": read_words(0x60000000, 32),
            }
            print(
                "STOCK_VECTOR_STATE "
                + json.dumps(
                    vector_state,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
'''


def main() -> int:
    text = PROBE.read_text(encoding="utf-8")
    if MARKER in text:
        print("stock vector-state diagnostic already materialized")
        return 0
    count = text.count(ANCHOR)
    if count != 1:
        raise SystemExit(
            f"expected one vector-state insertion anchor, found {count}"
        )
    PROBE.write_text(
        text.replace(ANCHOR, ANCHOR + INSERTION, 1),
        encoding="utf-8",
    )
    print("stock vector-state diagnostic materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
