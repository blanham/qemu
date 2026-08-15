#!/usr/bin/env python3
"""Classify progress at bootcode.bin's former 0x544 delay frontier.

Sampling only the program counter is insufficient here: the firmware returns
from the delay helper and later calls it again, so every sample can legitimately
land at the same instruction.  The delay start value in r3 changes only after a
return/re-entry, while r26 identifies the caller.  Together with an advancing
system-timer value in r2, those generations prove forward progress even when
the sampled PC is stable.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

FORMER_DELAY_PC = 0x544
UINT32_MASK = 0xFFFFFFFF
UINT32_HALF_RANGE = 1 << 31

T = TypeVar("T")


def compact_transitions(values: Sequence[T]) -> list[T]:
    result: list[T] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def u32_forward_step_count(values: Sequence[int]) -> int:
    """Count adjacent forward steps, accepting a single 32-bit wrap."""
    count = 0
    for previous, current in zip(values, values[1:]):
        delta = (current - previous) & UINT32_MASK
        if 0 < delta < UINT32_HALF_RANGE:
            count += 1
    return count


def analyze_snapshots(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    former_delay_pc: int = FORMER_DELAY_PC,
) -> dict[str, Any]:
    pcs: list[int] = []
    delay_generations: list[tuple[int, int]] = []
    delay_timer_values: list[int] = []

    for snapshot in snapshots:
        pc_value = snapshot.get("pc", -1)
        pc = int(pc_value) if isinstance(pc_value, int) else -1
        pcs.append(pc)
        if pc != former_delay_pc:
            continue

        registers = snapshot.get("registers")
        if not isinstance(registers, Mapping):
            continue

        timer = registers.get(2)
        start_timer = registers.get(3)
        return_pc = registers.get(26)
        if isinstance(timer, int):
            delay_timer_values.append(timer & UINT32_MASK)
        if isinstance(start_timer, int) and isinstance(return_pc, int):
            generation = (
                start_timer & UINT32_MASK,
                return_pc & UINT32_MASK,
            )
            if not delay_generations or delay_generations[-1] != generation:
                delay_generations.append(generation)

    valid_pcs = [pc for pc in pcs if pc >= 0]
    pc_transitions = compact_transitions(valid_pcs)
    left_former_delay_pc = any(pc != former_delay_pc for pc in valid_pcs)
    timer_forward_steps = u32_forward_step_count(delay_timer_values)
    delay_reentered = len(delay_generations) >= 2

    if left_former_delay_pc:
        crossed = True
        reason = "sampled-pc-left-former-delay-helper"
    elif delay_reentered and timer_forward_steps > 0:
        crossed = True
        reason = "delay-helper-returned-and-was-reentered"
    else:
        crossed = False
        reason = "insufficient-forward-progress-evidence"

    return {
        "schema_version": 1,
        "sample_count": len(snapshots),
        "former_delay_pc": former_delay_pc,
        "pc_transitions": pc_transitions,
        "left_former_delay_pc": left_former_delay_pc,
        "delay_generations": [
            {
                "start_timer": start_timer,
                "return_pc": return_pc,
            }
            for start_timer, return_pc in delay_generations
        ],
        "delay_generation_count": len(delay_generations),
        "delay_reentered": delay_reentered,
        "timer_forward_step_count": timer_forward_steps,
        "former_delay_frontier_crossed": crossed,
        "reason": reason,
    }


def _snapshot(pc: int, timer: int, start: int, return_pc: int) -> dict[str, Any]:
    return {
        "pc": pc,
        "registers": {
            2: timer,
            3: start,
            26: return_pc,
        },
    }


def selftest() -> None:
    steady = analyze_snapshots([
        _snapshot(FORMER_DELAY_PC, 100, 80, 0x5B0),
        _snapshot(FORMER_DELAY_PC, 120, 80, 0x5B0),
        _snapshot(FORMER_DELAY_PC, 140, 80, 0x5B0),
    ])
    assert not steady["former_delay_frontier_crossed"]

    reentered = analyze_snapshots([
        _snapshot(FORMER_DELAY_PC, 100, 80, 0x5B0),
        _snapshot(FORMER_DELAY_PC, 220, 200, 0x5A2),
    ])
    assert reentered["former_delay_frontier_crossed"]
    assert reentered["reason"] == "delay-helper-returned-and-was-reentered"

    left = analyze_snapshots([
        _snapshot(FORMER_DELAY_PC, 100, 80, 0x5B0),
        _snapshot(0x560, 110, 80, 0x5B0),
    ])
    assert left["former_delay_frontier_crossed"]
    assert left["reason"] == "sampled-pc-left-former-delay-helper"

    wrapped = analyze_snapshots([
        _snapshot(FORMER_DELAY_PC, 0xFFFFFFF0, 0xFFFFFF00, 0x5B0),
        _snapshot(FORMER_DELAY_PC, 0x00000020, 0x00000010, 0x5B0),
    ])
    assert wrapped["former_delay_frontier_crossed"]
    assert wrapped["timer_forward_step_count"] == 1

    print("stock bootcode progress classifier self-test passed")


if __name__ == "__main__":
    selftest()
