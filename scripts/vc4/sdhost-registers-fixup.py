#!/usr/bin/env python3
"""Materialize BCM2835 SDHOST register latches and documented reset values."""

from __future__ import annotations

from pathlib import Path

HEADER = Path("include/hw/sd/bcm2835_sdhost.h")
SOURCE = Path("hw/sd/bcm2835_sdhost.c")

HEADER_ANCHOR = r'''    uint32_t cmd;
    uint32_t cmdarg;
'''
HEADER_BLOCK = r'''    uint32_t cmd;
    uint32_t cmdarg;
    uint32_t timeout;
    uint32_t cdiv;
'''

CONSTANTS_ANCHOR = "#define SDCDIV_MAX_CDIV                 0x7ff\n"
CONSTANTS_BLOCK = r'''#define SDTOUT_RESET                    0x00a00000
#define SDCDIV_RESET                    0x000001fb
#define SDHBCT_RESET                    0x00000400
'''

READ_HEAD_OLD = r'''    case SDCMD:
        res = s->cmd;
        break;
    case SDHSTS:
        res = s->status;
        break;
'''
READ_HEAD_NEW = r'''    case SDCMD:
        res = s->cmd;
        break;
    case SDARG:
        res = s->cmdarg;
        break;
    case SDTOUT:
        res = s->timeout;
        break;
    case SDCDIV:
        res = s->cdiv;
        break;
    case SDHSTS:
        res = s->status;
        break;
'''

READ_CONFIG_OLD = r'''    case SDVDD:
        res = s->vdd;
        break;
    case SDDATA:
'''
READ_CONFIG_NEW = r'''    case SDVDD:
        res = s->vdd;
        break;
    case SDHCFG:
        res = s->config;
        break;
    case SDDATA:
'''

WRITE_LATCHES_OLD = r'''    case SDTOUT:
        break;
    case SDCDIV:
        break;
'''
WRITE_LATCHES_NEW = r'''    case SDTOUT:
        s->timeout = value;
        break;
    case SDCDIV:
        s->cdiv = value & SDCDIV_MAX_CDIV;
        break;
'''

VMSTATE_VERSION_OLD = r'''static const VMStateDescription vmstate_bcm2835_sdhost = {
    .name = TYPE_BCM2835_SDHOST,
    .version_id = 1,
    .minimum_version_id = 1,
'''
VMSTATE_VERSION_NEW = r'''static const VMStateDescription vmstate_bcm2835_sdhost = {
    .name = TYPE_BCM2835_SDHOST,
    .version_id = 2,
    .minimum_version_id = 1,
'''

VMSTATE_FIELDS_OLD = r'''        VMSTATE_UINT32(cmd, BCM2835SDHostState),
        VMSTATE_UINT32(cmdarg, BCM2835SDHostState),
        VMSTATE_UINT32(status, BCM2835SDHostState),
'''
VMSTATE_FIELDS_NEW = r'''        VMSTATE_UINT32(cmd, BCM2835SDHostState),
        VMSTATE_UINT32(cmdarg, BCM2835SDHostState),
        VMSTATE_UINT32_V(timeout, BCM2835SDHostState, 2),
        VMSTATE_UINT32_V(cdiv, BCM2835SDHostState, 2),
        VMSTATE_UINT32(status, BCM2835SDHostState),
'''

RESET_OLD = r'''    s->cmd = 0;
    s->cmdarg = 0;
    s->edm = 0x0000c60f;
    trace_bcm2835_sdhost_edm_change("device reset", s->edm);
    s->config = 0;
    s->hbct = 0;
    s->hblc = 0;
    s->datacnt = 0;
    s->fifo_pos = 0;
    s->fifo_len = 0;
'''
RESET_NEW = r'''    s->cmd = 0;
    s->cmdarg = 0;
    s->timeout = SDTOUT_RESET;
    s->cdiv = SDCDIV_RESET;
    s->status = 0;
    memset(s->rsp, 0, sizeof(s->rsp));
    s->edm = 0x0000c60f;
    trace_bcm2835_sdhost_edm_change("device reset", s->edm);
    s->config = 0;
    s->vdd = 0;
    s->hbct = SDHBCT_RESET;
    s->hblc = 0;
    s->datacnt = 0;
    s->fifo_pos = 0;
    s->fifo_len = 0;
    memset(s->fifo, 0, sizeof(s->fifo));
    bcm2835_sdhost_update_irq(s);
'''


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    new_count = text.count(new)
    if new_count == 1:
        return text, False
    if new_count > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {new_count}"
        )
    old_count = text.count(old)
    if old_count != 1:
        raise RuntimeError(f"expected one {label}, found {old_count}")
    return text.replace(old, new), True


def insert_after(text: str, anchor: str, block: str,
                 marker: str, label: str) -> tuple[str, bool]:
    marker_count = text.count(marker)
    if marker_count == 1:
        return text, False
    if marker_count > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {marker_count}"
        )
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(
            f"expected one {label} anchor, found {anchor_count}"
        )
    return text.replace(anchor, anchor + block), True


def update_header() -> bool:
    original = HEADER.read_text(encoding="utf-8")
    text, _ = replace_once(
        original, HEADER_ANCHOR, HEADER_BLOCK, "SDHOST timeout/divider state"
    )
    if text == original:
        return False
    HEADER.write_text(text, encoding="utf-8")
    return True


def update_source() -> bool:
    original = SOURCE.read_text(encoding="utf-8")
    text = original
    text, _ = insert_after(
        text,
        CONSTANTS_ANCHOR,
        CONSTANTS_BLOCK,
        "#define SDTOUT_RESET",
        "SDHOST reset constants",
    )
    text, _ = replace_once(
        text, READ_HEAD_OLD, READ_HEAD_NEW, "SDHOST argument/timing reads"
    )
    text, _ = replace_once(
        text, READ_CONFIG_OLD, READ_CONFIG_NEW, "SDHOST configuration read"
    )
    text, _ = replace_once(
        text, WRITE_LATCHES_OLD, WRITE_LATCHES_NEW, "SDHOST timing writes"
    )
    text, _ = replace_once(
        text,
        VMSTATE_VERSION_OLD,
        VMSTATE_VERSION_NEW,
        "SDHOST VMState version 2",
    )
    text, _ = replace_once(
        text,
        VMSTATE_FIELDS_OLD,
        VMSTATE_FIELDS_NEW,
        "SDHOST timing VMState fields",
    )
    text, _ = replace_once(
        text, RESET_OLD, RESET_NEW, "SDHOST documented reset state"
    )
    if text == original:
        return False
    SOURCE.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    changed = update_header() | update_source()
    if changed:
        print("Materialized BCM2835 SDHOST register latches and reset values.")
    else:
        print("BCM2835 SDHOST register latches and reset values are materialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
