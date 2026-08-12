#!/usr/bin/env python3
"""Materialize the BCM2835 CPRMAN SDRAM-clock UPDATE/ACCPT handshake.

The SDRAM clock-control register is mostly clock-mux compatible, but unlike
ordinary CM_*CTL registers it has UPDATE and read-only ACCPT bits above the
mux fields.  VideoCore firmware sets UPDATE and waits for ACCPT, then clears
UPDATE and waits for ACCPT to clear.  Complete both transitions immediately;
clock-rate changes remain handled by the existing clock-mux model.
"""

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
    """REG32(CM_SDCCTL, 0x1a8)
REG32(CM_SDCDIV, 0x1ac)
""",
    """REG32(CM_SDCCTL, 0x1a8)
    FIELD(CM_SDCCTL, UPDATE, 17, 1)
    FIELD(CM_SDCCTL, ACCPT, 16, 1)
    FIELD(CM_SDCCTL, CTRL, 12, 4)
REG32(CM_SDCDIV, 0x1ac)
""",
    "SDC clock-control register fields",
)

source = ROOT / "hw/misc/bcm2835_cprman.c"
replace_once(
    source,
    """    value &= ~R_CPRMAN_PASSWORD_MASK;

    trace_bcm2835_cprman_write(offset, value);
    s->regs[idx] = value;
""",
    """    value &= ~R_CPRMAN_PASSWORD_MASK;

    /*
     * CM_SDCCTL is not a plain CM_CLOCKx_CTL register.  UPDATE asks the
     * hardware to atomically accept a new SDRAM clock configuration and
     * ACCPT reports completion.  Firmware performs both edges as polling
     * handshakes.  The clock graph changes synchronously in QEMU, so mirror
     * UPDATE into the read-only ACCPT bit before publishing the register.
     */
    if (idx == R_CM_SDCCTL) {
        value = FIELD_DP32(value, CM_SDCCTL, ACCPT,
                           FIELD_EX32(value, CM_SDCCTL, UPDATE));
    }

    trace_bcm2835_cprman_write(offset, value);
    s->regs[idx] = value;
""",
    "CM_SDCCTL UPDATE/ACCPT write handling",
)

print("Materialized BCM2835 CPRMAN SDRAM clock UPDATE/ACCPT handshake.")
