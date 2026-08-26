#!/usr/bin/env python3
"""Exercise BCM2835 HVS channel state, vblank latching, and scanout."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from typing import Any


HVS_BASE = 0x3F400000
PIXELVALVES = (0x3F206000, 0x3F207000, 0x3F807000)
HVS_CHANNEL_TO_PIXELVALVE = (0, 2, 1)
PV_CONTROL = 0x00
PV_V_CONTROL = 0x04
PV_INTSTAT = 0x28

SCALER_DISPCTRL = HVS_BASE + 0x0000
SCALER_DISPSTAT = HVS_BASE + 0x0004
SCALER_DISPLIST = tuple(HVS_BASE + 0x0020 + index * 4 for index in range(3))
SCALER_DISPLACT = tuple(HVS_BASE + 0x0030 + index * 4 for index in range(3))
SCALER_DISPCTRLX = tuple(HVS_BASE + 0x0040 + index * 0x10 for index in range(3))
SCALER_DISPSTATX = tuple(HVS_BASE + 0x0048 + index * 0x10 for index in range(3))
SCALER_DLIST_START = HVS_BASE + 0x2000

SCALER_DISPCTRLX_ENABLE = 1 << 31
SCALER_DISPCTRLX_RESET = 1 << 30
SCALER_DISPSTATX_MODE_MASK = 3 << 30
SCALER_DISPSTATX_MODE_DISABLED = 0 << 30
SCALER_DISPSTATX_MODE_RUN = 2 << 30
SCALER_DISPSTATX_FULL = 1 << 29
SCALER_DISPSTATX_EMPTY = 1 << 28

PV_CONTROL_EN = 1 << 0
PV_VCONTROL_CONTINUOUS = 1 << 1
PV_VCONTROL_VIDEN = 1 << 0
PV_INT_VFP_START = 1 << 7
FRAME_STEP_NS = 20_000_000

SCALER_CTL0_END = 1 << 31
SCALER_CTL0_VALID = 1 << 30
SCALER_CTL0_RGBA_EXPAND_ROUND = 3 << 11
SCALER_CTL0_ORDER_RGBA = 0 << 13
SCALER_CTL0_ORDER_BGRA = 1 << 13
SCALER_CTL0_ORDER_ARGB = 2 << 13
SCALER_CTL0_ORDER_XRGB = 2 << 13
SCALER_CTL0_ORDER_ABGR = 3 << 13
SCALER_CTL0_ORDER_XBGR = 3 << 13
SCALER_CTL0_UNITY = 1 << 4
HVS_PIXEL_FORMAT_RGB565 = 4
HVS_PIXEL_FORMAT_RGB888 = 5
HVS_PIXEL_FORMAT_RGBA8888 = 7

SCANOUT_CHANNEL = 2
SCANOUT_WIDTH = 8
SCANOUT_HEIGHT = 4
SCANOUT_PITCH = SCANOUT_WIDTH * 4
SCANOUT_LIST_WORD = 0x100
SCANOUT_FLIP_LIST_WORD = 0x110
SCANOUT_BUFFER_A = 0x00200000
SCANOUT_BUFFER_B = 0x00201000
RGB565_SCANOUT_PITCH = SCANOUT_WIDTH * 2
RGB565_SCANOUT_LIST_WORD = 0x120
RGB565_SCANOUT_BUFFER_A = 0x00202000
RGB565_SCANOUT_BUFFER_B = 0x00203000
RGB888_SCANOUT_PITCH = SCANOUT_WIDTH * 3 + 12
RGB888_SCANOUT_LIST_WORD = 0x140
RGB888_SCANOUT_BUFFER_A = 0x00204000
RGB888_SCANOUT_BUFFER_B = 0x00205000
RGBA8888_SCANOUT_LIST_WORD = 0x160
RGBA8888_SCANOUT_BUFFER_A = 0x00206000
RGBA8888_SCANOUT_BUFFER_B = 0x00207000


class QMPClient:
    def __init__(self, path: Path) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(str(path))
        self._reader = self._sock.makefile("r", encoding="utf-8")
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"unexpected QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def close(self) -> None:
        self._reader.close()
        self._sock.close()

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self._reader.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            message = json.loads(line)
            if isinstance(message, dict):
                return message

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "execute": command,
            "id": command,
        }
        if arguments is not None:
            request["arguments"] = arguments
        self._sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        while True:
            response = self._read_message()
            if response.get("id") != command:
                continue
            if "error" in response:
                raise RuntimeError(
                    f"QMP {command} failed: {response['error']!r}"
                )
            return response.get("return")


def load_property_support() -> Any:
    support_path = Path(__file__).with_name("property-power-domain-smoke.py")
    spec = importlib.util.spec_from_file_location(
        "vc4_property_smoke_support", support_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"cannot load property smoke support: {support_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def advance_pixelvalve_frame(qtest: Any, channel: int) -> None:
    if not 0 <= channel < len(HVS_CHANNEL_TO_PIXELVALVE):
        raise ValueError(f"invalid HVS channel {channel}")

    pixelvalve = PIXELVALVES[HVS_CHANNEL_TO_PIXELVALVE[channel]]
    qtest.writel(pixelvalve + PV_INTSTAT, 0xFFFFFFFF)
    qtest.writel(pixelvalve + PV_CONTROL, PV_CONTROL_EN)
    qtest.writel(
        pixelvalve + PV_V_CONTROL,
        PV_VCONTROL_CONTINUOUS | PV_VCONTROL_VIDEN,
    )
    qtest.command(f"clock_step {FRAME_STEP_NS}")
    qtest.writel(pixelvalve + PV_INTSTAT, PV_INT_VFP_START)
    qtest.writel(pixelvalve + PV_V_CONTROL, 0)
    qtest.writel(pixelvalve + PV_CONTROL, 0)


def expect_disabled_empty(qtest: Any, channel: int) -> None:
    control = qtest.readl(SCALER_DISPCTRLX[channel])
    status = qtest.readl(SCALER_DISPSTATX[channel])
    if control != 0:
        raise RuntimeError(
            f"HVS channel {channel} control is not disabled: 0x{control:08x}"
        )
    if status & SCALER_DISPSTATX_MODE_MASK != SCALER_DISPSTATX_MODE_DISABLED:
        raise RuntimeError(
            f"HVS channel {channel} mode is not disabled: 0x{status:08x}"
        )
    if not status & SCALER_DISPSTATX_EMPTY:
        raise RuntimeError(
            f"HVS channel {channel} is not empty: 0x{status:08x}"
        )
    if status & SCALER_DISPSTATX_FULL:
        raise RuntimeError(
            f"HVS channel {channel} is both full and empty: 0x{status:08x}"
        )


def exercise_channel(qtest: Any, channel: int) -> None:
    first_list = 0x40 + channel * 0x20
    second_list = first_list + 0x10
    control = SCALER_DISPCTRLX_ENABLE | (640 << 12) | 480

    previous_active = qtest.readl(SCALER_DISPLACT[channel])
    qtest.writel(SCALER_DISPLIST[channel], first_list)
    if qtest.readl(SCALER_DISPLIST[channel]) != first_list:
        raise RuntimeError(f"HVS channel {channel} did not retain DISPLIST")
    if qtest.readl(SCALER_DISPLACT[channel]) != previous_active:
        raise RuntimeError(
            f"HVS channel {channel} latched DISPLACT while disabled"
        )

    qtest.writel(SCALER_DISPCTRLX[channel], SCALER_DISPCTRLX_RESET)
    expect_disabled_empty(qtest, channel)

    qtest.writel(SCALER_DISPCTRLX[channel], control)
    read_control = qtest.readl(SCALER_DISPCTRLX[channel])
    if not read_control & SCALER_DISPCTRLX_ENABLE:
        raise RuntimeError(f"HVS channel {channel} did not enable")
    if read_control & SCALER_DISPCTRLX_RESET:
        raise RuntimeError(f"HVS channel {channel} RESET did not self-clear")
    status = qtest.readl(SCALER_DISPSTATX[channel])
    if status & SCALER_DISPSTATX_MODE_MASK != SCALER_DISPSTATX_MODE_RUN:
        raise RuntimeError(
            f"HVS channel {channel} did not enter RUN mode: 0x{status:08x}"
        )
    if not status & SCALER_DISPSTATX_FULL or status & SCALER_DISPSTATX_EMPTY:
        raise RuntimeError(
            f"HVS channel {channel} FIFO state is invalid: 0x{status:08x}"
        )
    if qtest.readl(SCALER_DISPLACT[channel]) != first_list:
        raise RuntimeError(
            f"HVS channel {channel} did not activate its initial display list"
        )

    qtest.writel(SCALER_DISPLIST[channel], second_list)
    if qtest.readl(SCALER_DISPLACT[channel]) != first_list:
        raise RuntimeError(
            f"HVS channel {channel} latched replacement before vblank"
        )

    advance_pixelvalve_frame(qtest, channel)
    if qtest.readl(SCALER_DISPLACT[channel]) != second_list:
        raise RuntimeError(
            f"HVS channel {channel} did not latch replacement at vblank"
        )

    qtest.writel(SCALER_DISPCTRLX[channel], 0)
    expect_disabled_empty(qtest, channel)


def write_pattern(
    qtest: Any,
    base: int,
    colors: tuple[int, int, int, int],
) -> None:
    for y in range(SCANOUT_HEIGHT):
        for x in range(SCANOUT_WIDTH):
            quadrant = (2 if y >= SCANOUT_HEIGHT // 2 else 0) + (
                1 if x >= SCANOUT_WIDTH // 2 else 0
            )
            qtest.writel(base + y * SCANOUT_PITCH + x * 4, colors[quadrant])


def write_rgb565_pattern(
    qtest: Any,
    base: int,
    colors: tuple[int, int, int, int],
) -> None:
    if SCANOUT_WIDTH % 2:
        raise RuntimeError("RGB565 smoke width must be even")

    for y in range(SCANOUT_HEIGHT):
        for x in range(0, SCANOUT_WIDTH, 2):
            first_quadrant = (2 if y >= SCANOUT_HEIGHT // 2 else 0) + (
                1 if x >= SCANOUT_WIDTH // 2 else 0
            )
            second_x = x + 1
            second_quadrant = (2 if y >= SCANOUT_HEIGHT // 2 else 0) + (
                1 if second_x >= SCANOUT_WIDTH // 2 else 0
            )
            packed = colors[first_quadrant] | (
                colors[second_quadrant] << 16
            )
            qtest.writel(
                base + y * RGB565_SCANOUT_PITCH + x * 2,
                packed,
            )


def write_rgb888_pattern(
    qtest: Any,
    base: int,
    colors: tuple[tuple[int, int, int], ...],
    order: int,
) -> None:
    row_bytes = SCANOUT_WIDTH * 3

    if row_bytes % 4:
        raise RuntimeError("RGB888 smoke rows must be qtest-word aligned")
    if RGB888_SCANOUT_PITCH < row_bytes:
        raise RuntimeError("RGB888 smoke pitch is smaller than one row")

    for y in range(SCANOUT_HEIGHT):
        row = bytearray()

        for x in range(SCANOUT_WIDTH):
            quadrant = (2 if y >= SCANOUT_HEIGHT // 2 else 0) + (
                1 if x >= SCANOUT_WIDTH // 2 else 0
            )
            red, green, blue = colors[quadrant]
            if order == SCALER_CTL0_ORDER_XRGB:
                row.extend((blue, green, red))
            elif order == SCALER_CTL0_ORDER_XBGR:
                row.extend((red, green, blue))
            else:
                raise RuntimeError(f"unsupported RGB888 order 0x{order:x}")

        for offset in range(0, len(row), 4):
            qtest.writel(
                base + y * RGB888_SCANOUT_PITCH + offset,
                int.from_bytes(row[offset:offset + 4], "little"),
            )


def write_rgba8888_pattern(
    qtest: Any,
    base: int,
    colors: tuple[tuple[int, int, int], ...],
    order: int,
) -> None:
    alpha = 0xFF

    for y in range(SCANOUT_HEIGHT):
        for x in range(SCANOUT_WIDTH):
            quadrant = (2 if y >= SCANOUT_HEIGHT // 2 else 0) + (
                1 if x >= SCANOUT_WIDTH // 2 else 0
            )
            red, green, blue = colors[quadrant]
            if order == SCALER_CTL0_ORDER_ABGR:
                memory = (blue, green, red, alpha)
            elif order == SCALER_CTL0_ORDER_ARGB:
                memory = (red, green, blue, alpha)
            elif order == SCALER_CTL0_ORDER_RGBA:
                memory = (alpha, blue, green, red)
            elif order == SCALER_CTL0_ORDER_BGRA:
                memory = (alpha, red, green, blue)
            else:
                raise RuntimeError(
                    f"unsupported RGBA8888 order 0x{order:x}"
                )
            qtest.writel(
                base + y * SCANOUT_PITCH + x * 4,
                int.from_bytes(memory, "little"),
            )


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    position = 0

    def token() -> bytes:
        nonlocal position
        while position < len(data):
            if data[position] == ord("#"):
                newline = data.find(b"\n", position)
                if newline < 0:
                    raise RuntimeError(f"unterminated PPM comment in {path}")
                position = newline + 1
                continue
            if data[position] in b" \t\r\n":
                position += 1
                continue
            break
        start = position
        while position < len(data) and data[position] not in b" \t\r\n":
            position += 1
        if start == position:
            raise RuntimeError(f"truncated PPM header in {path}")
        return data[start:position]

    if token() != b"P6":
        raise RuntimeError(f"QEMU screendump is not binary PPM: {path}")
    width = int(token())
    height = int(token())
    maximum = int(token())
    if maximum != 255:
        raise RuntimeError(f"unsupported PPM maximum {maximum} in {path}")
    if position >= len(data) or data[position] not in b" \t\r\n":
        raise RuntimeError(f"missing PPM payload separator in {path}")
    if data[position:position + 2] == b"\r\n":
        position += 2
    else:
        position += 1
    pixels = data[position:]
    expected = width * height * 3
    if len(pixels) != expected:
        raise RuntimeError(
            f"PPM payload size mismatch in {path}: {len(pixels)} != {expected}"
        )
    return width, height, pixels


def expect_pattern(
    path: Path,
    expected_colors: tuple[tuple[int, int, int], ...],
) -> None:
    width, height, pixels = read_ppm(path)
    if (width, height) != (SCANOUT_WIDTH, SCANOUT_HEIGHT):
        raise RuntimeError(
            f"HVS scanout size mismatch: {(width, height)} != "
            f"{(SCANOUT_WIDTH, SCANOUT_HEIGHT)}"
        )

    samples = (
        (1, 1),
        (width - 2, 1),
        (1, height - 2),
        (width - 2, height - 2),
    )
    for (x, y), expected in zip(samples, expected_colors, strict=True):
        offset = (y * width + x) * 3
        actual = tuple(pixels[offset:offset + 3])
        if actual != expected:
            raise RuntimeError(
                f"HVS scanout pixel {(x, y)} is {actual}, expected {expected}"
            )


def exercise_scanout(qtest: Any, qmp: QMPClient, temp: Path) -> None:
    colors_a = (0x00FF0000, 0x0000FF00, 0x000000FF, 0x00FFFFFF)
    colors_b = (0x00FFFFFF, 0x000000FF, 0x0000FF00, 0x00FF0000)
    expected_a = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    expected_b = ((255, 255, 255), (0, 0, 255), (0, 255, 0), (255, 0, 0))
    dlist = SCALER_DLIST_START + SCANOUT_LIST_WORD * 4
    flip_dlist = SCALER_DLIST_START + SCANOUT_FLIP_LIST_WORD * 4
    ctl0 = (
        SCALER_CTL0_VALID
        | (7 << 24)
        | SCALER_CTL0_ORDER_ABGR
        | SCALER_CTL0_RGBA_EXPAND_ROUND
        | SCALER_CTL0_UNITY
        | HVS_PIXEL_FORMAT_RGBA8888
    )
    element = (
        ctl0,
        0xFF000000,
        (SCANOUT_HEIGHT << 16) | SCANOUT_WIDTH,
        0xC0C0C0C0,
        SCANOUT_BUFFER_A,
        0xC0C0C0C0,
        SCANOUT_PITCH,
        SCALER_CTL0_END,
    )
    flip_element = (
        ctl0,
        0xFF000000,
        (SCANOUT_HEIGHT << 16) | SCANOUT_WIDTH,
        0xC0C0C0C0,
        SCANOUT_BUFFER_B,
        0xC0C0C0C0,
        SCANOUT_PITCH,
        SCALER_CTL0_END,
    )

    write_pattern(qtest, SCANOUT_BUFFER_A, colors_a)
    write_pattern(qtest, SCANOUT_BUFFER_B, colors_b)
    for index, value in enumerate(element):
        qtest.writel(dlist + index * 4, value)
    for index, value in enumerate(flip_element):
        qtest.writel(flip_dlist + index * 4, value)

    qtest.writel(SCALER_DISPLIST[SCANOUT_CHANNEL], SCANOUT_LIST_WORD)
    qtest.writel(
        SCALER_DISPCTRLX[SCANOUT_CHANNEL],
        SCALER_DISPCTRLX_ENABLE
        | (SCANOUT_WIDTH << 12)
        | SCANOUT_HEIGHT,
    )

    first = temp / "hvs-scanout-a.ppm"
    qmp.execute("screendump", {"filename": str(first)})
    expect_pattern(first, expected_a)

    qtest.writel(
        dlist + 4 * 4,
        SCANOUT_BUFFER_B,
    )
    second = temp / "hvs-scanout-b.ppm"
    qmp.execute("screendump", {"filename": str(second)})
    expect_pattern(second, expected_b)

    # Async flips rewrite the address in the active element and take effect
    # immediately.  A normal atomic flip installs a new list and waits for the
    # mapped pixel valve's next frame boundary before DISPLACT changes.
    qtest.writel(
        dlist + 4 * 4,
        SCANOUT_BUFFER_A,
    )
    qtest.writel(
        SCALER_DISPLIST[SCANOUT_CHANNEL],
        SCANOUT_FLIP_LIST_WORD,
    )
    if qtest.readl(SCALER_DISPLACT[SCANOUT_CHANNEL]) != SCANOUT_LIST_WORD:
        raise RuntimeError("HVS normal flip latched before vblank")

    before_vblank = temp / "hvs-scanout-before-vblank.ppm"
    qmp.execute("screendump", {"filename": str(before_vblank)})
    expect_pattern(before_vblank, expected_a)

    advance_pixelvalve_frame(qtest, SCANOUT_CHANNEL)
    if (
        qtest.readl(SCALER_DISPLACT[SCANOUT_CHANNEL])
        != SCANOUT_FLIP_LIST_WORD
    ):
        raise RuntimeError("HVS normal flip did not latch at vblank")

    after_vblank = temp / "hvs-scanout-after-vblank.ppm"
    qmp.execute("screendump", {"filename": str(after_vblank)})
    expect_pattern(after_vblank, expected_b)

    qtest.writel(SCALER_DISPCTRLX[SCANOUT_CHANNEL], 0)
    expect_disabled_empty(qtest, SCANOUT_CHANNEL)


def exercise_rgb565_scanout(
    qtest: Any,
    qmp: QMPClient,
    temp: Path,
) -> None:
    colors_a = (0xF800, 0x07E0, 0x001F, 0xFFFF)
    colors_b = (0xFFFF, 0x001F, 0x07E0, 0xF800)
    expected_a = (
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 255),
    )
    expected_b = (
        (255, 255, 255),
        (0, 0, 255),
        (0, 255, 0),
        (255, 0, 0),
    )
    dlist = SCALER_DLIST_START + RGB565_SCANOUT_LIST_WORD * 4
    ctl0 = (
        SCALER_CTL0_VALID
        | (7 << 24)
        | SCALER_CTL0_ORDER_XRGB
        | SCALER_CTL0_RGBA_EXPAND_ROUND
        | SCALER_CTL0_UNITY
        | HVS_PIXEL_FORMAT_RGB565
    )
    element = (
        ctl0,
        0xFF000000,
        (SCANOUT_HEIGHT << 16) | SCANOUT_WIDTH,
        0xC0C0C0C0,
        RGB565_SCANOUT_BUFFER_A,
        0xC0C0C0C0,
        RGB565_SCANOUT_PITCH,
        SCALER_CTL0_END,
    )

    write_rgb565_pattern(qtest, RGB565_SCANOUT_BUFFER_A, colors_a)
    write_rgb565_pattern(qtest, RGB565_SCANOUT_BUFFER_B, colors_b)
    for index, value in enumerate(element):
        qtest.writel(dlist + index * 4, value)

    qtest.writel(
        SCALER_DISPLIST[SCANOUT_CHANNEL],
        RGB565_SCANOUT_LIST_WORD,
    )
    qtest.writel(
        SCALER_DISPCTRLX[SCANOUT_CHANNEL],
        SCALER_DISPCTRLX_ENABLE
        | (SCANOUT_WIDTH << 12)
        | SCANOUT_HEIGHT,
    )

    first = temp / "hvs-rgb565-scanout-a.ppm"
    qmp.execute("screendump", {"filename": str(first)})
    expect_pattern(first, expected_a)

    qtest.writel(
        dlist + 4 * 4,
        RGB565_SCANOUT_BUFFER_B,
    )
    second = temp / "hvs-rgb565-scanout-b.ppm"
    qmp.execute("screendump", {"filename": str(second)})
    expect_pattern(second, expected_b)

    qtest.writel(SCALER_DISPCTRLX[SCANOUT_CHANNEL], 0)
    expect_disabled_empty(qtest, SCANOUT_CHANNEL)


def exercise_rgb888_scanout(
    qtest: Any,
    qmp: QMPClient,
    temp: Path,
) -> None:
    colors_a = (
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 255),
    )
    colors_b = tuple(reversed(colors_a))
    dlist = SCALER_DLIST_START + RGB888_SCANOUT_LIST_WORD * 4

    for label, order in (
        ("rgb888", SCALER_CTL0_ORDER_XRGB),
        ("bgr888", SCALER_CTL0_ORDER_XBGR),
    ):
        ctl0 = (
            SCALER_CTL0_VALID
            | (7 << 24)
            | order
            | SCALER_CTL0_RGBA_EXPAND_ROUND
            | SCALER_CTL0_UNITY
            | HVS_PIXEL_FORMAT_RGB888
        )
        element = (
            ctl0,
            0xFF000000,
            (SCANOUT_HEIGHT << 16) | SCANOUT_WIDTH,
            0xC0C0C0C0,
            RGB888_SCANOUT_BUFFER_A,
            0xC0C0C0C0,
            RGB888_SCANOUT_PITCH,
            SCALER_CTL0_END,
        )

        write_rgb888_pattern(
            qtest,
            RGB888_SCANOUT_BUFFER_A,
            colors_a,
            order,
        )
        write_rgb888_pattern(
            qtest,
            RGB888_SCANOUT_BUFFER_B,
            colors_b,
            order,
        )
        for index, value in enumerate(element):
            qtest.writel(dlist + index * 4, value)

        qtest.writel(
            SCALER_DISPLIST[SCANOUT_CHANNEL],
            RGB888_SCANOUT_LIST_WORD,
        )
        qtest.writel(
            SCALER_DISPCTRLX[SCANOUT_CHANNEL],
            SCALER_DISPCTRLX_ENABLE
            | (SCANOUT_WIDTH << 12)
            | SCANOUT_HEIGHT,
        )

        first = temp / f"hvs-{label}-scanout-a.ppm"
        qmp.execute("screendump", {"filename": str(first)})
        expect_pattern(first, colors_a)

        qtest.writel(
            dlist + 4 * 4,
            RGB888_SCANOUT_BUFFER_B,
        )
        second = temp / f"hvs-{label}-scanout-b.ppm"
        qmp.execute("screendump", {"filename": str(second)})
        expect_pattern(second, colors_b)

        qtest.writel(SCALER_DISPCTRLX[SCANOUT_CHANNEL], 0)
        expect_disabled_empty(qtest, SCANOUT_CHANNEL)


def exercise_rgba8888_orders(
    qtest: Any,
    qmp: QMPClient,
    temp: Path,
) -> None:
    colors_a = (
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 255),
    )
    colors_b = tuple(reversed(colors_a))
    dlist = SCALER_DLIST_START + RGBA8888_SCANOUT_LIST_WORD * 4

    for label, order in (
        ("rgba", SCALER_CTL0_ORDER_RGBA),
        ("bgra", SCALER_CTL0_ORDER_BGRA),
        ("argb", SCALER_CTL0_ORDER_ARGB),
        ("abgr", SCALER_CTL0_ORDER_ABGR),
    ):
        ctl0 = (
            SCALER_CTL0_VALID
            | (7 << 24)
            | order
            | SCALER_CTL0_RGBA_EXPAND_ROUND
            | SCALER_CTL0_UNITY
            | HVS_PIXEL_FORMAT_RGBA8888
        )
        element = (
            ctl0,
            0xFF000000,
            (SCANOUT_HEIGHT << 16) | SCANOUT_WIDTH,
            0xC0C0C0C0,
            RGBA8888_SCANOUT_BUFFER_A,
            0xC0C0C0C0,
            SCANOUT_PITCH,
            SCALER_CTL0_END,
        )

        write_rgba8888_pattern(
            qtest,
            RGBA8888_SCANOUT_BUFFER_A,
            colors_a,
            order,
        )
        write_rgba8888_pattern(
            qtest,
            RGBA8888_SCANOUT_BUFFER_B,
            colors_b,
            order,
        )
        for index, value in enumerate(element):
            qtest.writel(dlist + index * 4, value)

        qtest.writel(
            SCALER_DISPLIST[SCANOUT_CHANNEL],
            RGBA8888_SCANOUT_LIST_WORD,
        )
        qtest.writel(
            SCALER_DISPCTRLX[SCANOUT_CHANNEL],
            SCALER_DISPCTRLX_ENABLE
            | (SCANOUT_WIDTH << 12)
            | SCANOUT_HEIGHT,
        )

        first = temp / f"hvs-rgba8888-{label}-a.ppm"
        qmp.execute("screendump", {"filename": str(first)})
        expect_pattern(first, colors_a)

        qtest.writel(
            dlist + 4 * 4,
            RGBA8888_SCANOUT_BUFFER_B,
        )
        second = temp / f"hvs-rgba8888-{label}-b.ppm"
        qmp.execute("screendump", {"filename": str(second)})
        expect_pattern(second, colors_b)

        qtest.writel(SCALER_DISPCTRLX[SCANOUT_CHANNEL], 0)
        expect_disabled_empty(qtest, SCANOUT_CHANNEL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-aarch64"),
        help="path to qemu-system-aarch64",
    )
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    support = load_property_support()

    with tempfile.TemporaryDirectory(prefix="vc4-hvs-dlist-") as temp_dir:
        temp = Path(temp_dir)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
        process = subprocess.Popen(
            (
                str(qemu),
                "-M",
                "raspi3b",
                "-accel",
                "qtest",
                "-S",
                "-display",
                "none",
                "-serial",
                "none",
                "-monitor",
                "none",
                "-qtest",
                f"unix:{qtest_path},server=on,wait=off",
                "-qmp",
                f"unix:{qmp_path},server=on,wait=off",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        qtest = None
        qmp = None
        try:
            qtest = support.connect_when_ready(
                qtest_path, process, support.QTestClient
            )
            qmp = support.connect_when_ready(qmp_path, process, QMPClient)
            qmp.execute("cont")

            if qtest.readl(SCALER_DISPCTRL) != 0:
                raise RuntimeError("HVS global control did not reset to zero")
            if qtest.readl(SCALER_DISPSTAT) != 0:
                raise RuntimeError("HVS global status did not reset to zero")
            for channel in range(3):
                expect_disabled_empty(qtest, channel)
                if qtest.readl(SCALER_DISPLIST[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} DISPLIST was not reset"
                    )
                if qtest.readl(SCALER_DISPLACT[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} DISPLACT was not reset"
                    )

            for index, value in enumerate((0x11223344, 0x55667788, 0xA5A55A5A)):
                address = SCALER_DLIST_START + index * 4
                qtest.writel(address, value)
                if qtest.readl(address) != value:
                    raise RuntimeError(
                        f"HVS DLIST RAM did not retain word {index}"
                    )

            exercise_scanout(qtest, qmp, temp)
            exercise_rgb565_scanout(qtest, qmp, temp)
            exercise_rgb888_scanout(qtest, qmp, temp)
            exercise_rgba8888_orders(qtest, qmp, temp)

            for channel in range(3):
                exercise_channel(qtest, channel)

            qmp.execute("system_reset")
            if qtest.readl(SCALER_DISPCTRL) != 0:
                raise RuntimeError("HVS global control survived system reset")
            for channel in range(3):
                expect_disabled_empty(qtest, channel)
                if qtest.readl(SCALER_DISPLIST[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} list survived system reset"
                    )
                if qtest.readl(SCALER_DISPLACT[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} active list survived "
                        "system reset"
                    )
            if qtest.readl(SCALER_DLIST_START) != 0:
                raise RuntimeError("HVS DLIST RAM survived system reset")
        finally:
            if qmp is not None:
                try:
                    qmp.execute("quit")
                except (OSError, RuntimeError):
                    pass
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        if process.returncode not in (0, None):
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(
                f"QEMU exited with status {process.returncode}:\n{stderr}"
            )

    print("BCM2835 HVS display-list and linear scanout smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
