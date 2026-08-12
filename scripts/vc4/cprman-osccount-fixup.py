#!/usr/bin/env python3
"""Materialize the BCM2835 CPRMAN oscillator countdown contract.

CM_OSCCOUNT is loaded with a number of 19.2 MHz oscillator cycles and then
polled until it reaches zero.  Model that finite handshake deterministically by
returning the current count and consuming one abstract oscillator interval per
MMIO read.  The count remains in CPRMAN's existing migrated register array.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


internals = ROOT / "include/hw/misc/bcm2835_cprman_internals.h"
replace_once(
    internals,
    """/* Register map */

/* PLLs */
""",
    """/* Register map */

/* Oscillator-cycle countdown used by clock-measurement and settle delays. */
REG32(CM_OSCCOUNT, 0x100)

/* PLLs */
""",
    "CM_OSCCOUNT register declaration",
)

source = ROOT / "hw/misc/bcm2835_cprman.c"
replace_once(
    source,
    """    switch (idx) {
    case R_CM_LOCK:
        r = get_cm_lock(s);
        break;

    default:
        r = s->regs[idx];
    }
""",
    """    switch (idx) {
    case R_CM_OSCCOUNT:
        /*
         * Hardware consumes this count at the 19.2 MHz crystal rate while
         * firmware polls it.  A read-driven countdown is deterministic under
         * TCG and migration: expose the current value, consume one abstract
         * oscillator interval, and saturate at zero.  This preserves the
         * visible finite polling protocol without host-time dependencies.
         */
        r = s->regs[idx];
        if (r != 0) {
            s->regs[idx] = r - 1;
        }
        break;

    case R_CM_LOCK:
        r = get_cm_lock(s);
        break;

    default:
        r = s->regs[idx];
    }
""",
    "CM_OSCCOUNT read countdown",
)

print("Materialized BCM2835 CPRMAN oscillator countdown.")
