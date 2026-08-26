#!/usr/bin/env python3
"""Rebase the staged VC4 packed-RGB patch onto the current branch."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def replace_once(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one semantic preimage, found {count}"
        )
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def extract_new_file(patch: str, relative_path: str) -> str:
    marker = f"diff --git a/{relative_path} b/{relative_path}\n"
    start = patch.find(marker)
    if start < 0:
        raise RuntimeError(f"patch does not contain {relative_path}")
    end = patch.find("\ndiff --git ", start + len(marker))
    section = patch[start:] if end < 0 else patch[start:end + 1]

    if f"new file mode " not in section:
        raise RuntimeError(f"{relative_path}: patch entry is not a new file")
    if f"+++ b/{relative_path}\n" not in section:
        raise RuntimeError(f"{relative_path}: malformed new-file patch")

    output: list[str] = []
    for line in section.splitlines(keepends=True):
        if line.startswith("+") and not line.startswith("+++"):
            output.append(line[1:])

    if not output:
        raise RuntimeError(f"{relative_path}: no added content found")
    return "".join(output)


def write_new_from_patch(
    patch: str,
    relative_path: str,
    mode: int,
) -> None:
    path = Path(relative_path)
    if path.exists():
        raise RuntimeError(f"{path}: refusing to overwrite existing file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        extract_new_file(patch, relative_path),
        encoding="utf-8",
    )
    os.chmod(path, mode)


def rebase_framebuffer_header() -> None:
    replace_once(
        Path("include/hw/display/bcm2835_fb.h"),
        """/*
 * Framebuffer source-byte orders.  Values 0 and 1 retain the mailbox
 * ABI; values 2 and 3 describe the HVS-only 32-bit byte rotations.
 */
#define BCM2835_FB_PIXEL_ORDER_BGR   0
#define BCM2835_FB_PIXEL_ORDER_RGB   1
#define BCM2835_FB_PIXEL_ORDER_RGBA  2
#define BCM2835_FB_PIXEL_ORDER_BGRA  3
""",
        """/*
 * Framebuffer source layouts.  Values 0 and 1 retain the mailbox ABI;
 * the remaining values describe HVS-only packed-RGB layouts.
 */
#define BCM2835_FB_PIXEL_ORDER_BGR           0
#define BCM2835_FB_PIXEL_ORDER_RGB           1
#define BCM2835_FB_PIXEL_ORDER_RGBA          2
#define BCM2835_FB_PIXEL_ORDER_BGRA          3
#define BCM2835_FB_PIXEL_ORDER_HVS_RGB565    4
#define BCM2835_FB_PIXEL_ORDER_HVS_BGR565    5
#define BCM2835_FB_PIXEL_ORDER_HVS_RGB332    6
#define BCM2835_FB_PIXEL_ORDER_HVS_BGR233    7
#define BCM2835_FB_PIXEL_ORDER_HVS_XRGB4444  8
#define BCM2835_FB_PIXEL_ORDER_HVS_XBGR4444  9
#define BCM2835_FB_PIXEL_ORDER_HVS_BGRX4444 10
#define BCM2835_FB_PIXEL_ORDER_HVS_RGBX4444 11
#define BCM2835_FB_PIXEL_ORDER_HVS_XRGB1555 12
""",
    )


def rebase_framebuffer_conversion() -> None:
    path = Path("hw/display/bcm2835_fb.c")
    replace_once(
        path,
        """static void fb_invalidate_display(void *opaque)
{
    BCM2835FBState *s = BCM2835_FB(opaque);

    s->invalidate = true;
}

static void draw_line_src16(void *opaque, uint8_t *dst, const uint8_t *src,
""",
        """static void fb_invalidate_display(void *opaque)
{
    BCM2835FBState *s = BCM2835_FB(opaque);

    s->invalidate = true;
}

static uint8_t bcm2835_fb_expand_component(uint32_t value, unsigned bits)
{
    uint32_t maximum = (UINT32_C(1) << bits) - 1;

    return value * UINT8_MAX / maximum;
}

static void draw_line_src16(void *opaque, uint8_t *dst, const uint8_t *src,
""",
    )
    replace_once(
        path,
        """    uint16_t rgb565;
    uint32_t rgb888;
    uint8_t r, g, b;
""",
        """    uint16_t rgb565;
    uint32_t rgb888;
    uint8_t packed;
    uint8_t r, g, b;
""",
    )
    replace_once(
        path,
        """        case 8:
            /* lookup palette starting at video ram base
             * TODO: cache translation, rather than doing this each time!
             */
            rgb888 = ldl_le_phys(&s->dma_as, s->vcram_base + (*src << 2));
            r = (rgb888 >> 0) & 0xff;
            g = (rgb888 >> 8) & 0xff;
            b = (rgb888 >> 16) & 0xff;
            src++;
            break;
        case 16:
            rgb565 = lduw_le_p(src);
            r = ((rgb565 >> 11) & 0x1f) << 3;
            g = ((rgb565 >>  5) & 0x3f) << 2;
            b = ((rgb565 >>  0) & 0x1f) << 3;
            src += 2;
            break;
        case 24:
""",
        """        case 8:
            packed = *src++;
            if (s->config.pixo == BCM2835_FB_PIXEL_ORDER_HVS_RGB332) {
                r = bcm2835_fb_expand_component((packed >> 5) & 0x7, 3);
                g = bcm2835_fb_expand_component((packed >> 2) & 0x7, 3);
                b = bcm2835_fb_expand_component(packed & 0x3, 2);
            } else if (s->config.pixo ==
                       BCM2835_FB_PIXEL_ORDER_HVS_BGR233) {
                r = bcm2835_fb_expand_component(packed & 0x7, 3);
                g = bcm2835_fb_expand_component((packed >> 3) & 0x7, 3);
                b = bcm2835_fb_expand_component((packed >> 6) & 0x3, 2);
            } else {
                /* lookup palette starting at video ram base
                 * TODO: cache translation, rather than doing this each time!
                 */
                rgb888 = ldl_le_phys(&s->dma_as,
                                     s->vcram_base + (packed << 2));
                r = (rgb888 >> 0) & 0xff;
                g = (rgb888 >> 8) & 0xff;
                b = (rgb888 >> 16) & 0xff;
            }
            break;
        case 16:
            rgb565 = lduw_le_p(src);
            src += 2;
            switch (s->config.pixo) {
            case BCM2835_FB_PIXEL_ORDER_HVS_RGB565:
                r = bcm2835_fb_expand_component((rgb565 >> 11) & 0x1f, 5);
                g = bcm2835_fb_expand_component((rgb565 >> 5) & 0x3f, 6);
                b = bcm2835_fb_expand_component(rgb565 & 0x1f, 5);
                break;
            case BCM2835_FB_PIXEL_ORDER_HVS_BGR565:
                r = bcm2835_fb_expand_component(rgb565 & 0x1f, 5);
                g = bcm2835_fb_expand_component((rgb565 >> 5) & 0x3f, 6);
                b = bcm2835_fb_expand_component((rgb565 >> 11) & 0x1f, 5);
                break;
            case BCM2835_FB_PIXEL_ORDER_HVS_XRGB4444:
                r = bcm2835_fb_expand_component((rgb565 >> 8) & 0xf, 4);
                g = bcm2835_fb_expand_component((rgb565 >> 4) & 0xf, 4);
                b = bcm2835_fb_expand_component(rgb565 & 0xf, 4);
                break;
            case BCM2835_FB_PIXEL_ORDER_HVS_XBGR4444:
                r = bcm2835_fb_expand_component(rgb565 & 0xf, 4);
                g = bcm2835_fb_expand_component((rgb565 >> 4) & 0xf, 4);
                b = bcm2835_fb_expand_component((rgb565 >> 8) & 0xf, 4);
                break;
            case BCM2835_FB_PIXEL_ORDER_HVS_BGRX4444:
                r = bcm2835_fb_expand_component((rgb565 >> 4) & 0xf, 4);
                g = bcm2835_fb_expand_component((rgb565 >> 8) & 0xf, 4);
                b = bcm2835_fb_expand_component((rgb565 >> 12) & 0xf, 4);
                break;
            case BCM2835_FB_PIXEL_ORDER_HVS_RGBX4444:
                r = bcm2835_fb_expand_component((rgb565 >> 12) & 0xf, 4);
                g = bcm2835_fb_expand_component((rgb565 >> 8) & 0xf, 4);
                b = bcm2835_fb_expand_component((rgb565 >> 4) & 0xf, 4);
                break;
            case BCM2835_FB_PIXEL_ORDER_HVS_XRGB1555:
                r = bcm2835_fb_expand_component((rgb565 >> 10) & 0x1f, 5);
                g = bcm2835_fb_expand_component((rgb565 >> 5) & 0x1f, 5);
                b = bcm2835_fb_expand_component(rgb565 & 0x1f, 5);
                break;
            default:
                r = ((rgb565 >> 11) & 0x1f) << 3;
                g = ((rgb565 >> 5) & 0x3f) << 2;
                b = (rgb565 & 0x1f) << 3;
                break;
            }
            break;
        case 24:
""",
    )


def rebase_hvs_parser() -> None:
    path = Path("hw/display/bcm2835_hvs.c")
    replace_once(
        path,
        """ * KMS scanout contract: one full-screen, unity-scaled, linear RGB565, RGB888,
 * or RGBA8888 plane.
""",
        """ * KMS scanout contract: one full-screen, unity-scaled, linear packed-RGB
 * plane using a layout advertised by the Linux VC4 driver.
""",
    )
    replace_once(
        path,
        """#define SCALER_CTL0_ORDER_SHIFT      13
#define SCALER_CTL0_ORDER_MASK       UINT32_C(0x3)
#define SCALER_CTL0_UNITY            BIT(4)
""",
        """#define SCALER_CTL0_ORDER_SHIFT      13
#define SCALER_CTL0_ORDER_MASK       UINT32_C(0x3)
#define SCALER_CTL0_RGBA_EXPAND_SHIFT 11
#define SCALER_CTL0_RGBA_EXPAND_MASK UINT32_C(0x3)
#define SCALER_CTL0_RGBA_EXPAND_ROUND UINT32_C(3)
#define SCALER_CTL0_UNITY            BIT(4)
""",
    )
    replace_once(
        path,
        """#define HVS_PIXEL_ORDER_XRGB         UINT32_C(2)
#define HVS_PIXEL_ORDER_XBGR         UINT32_C(3)
#define HVS_PIXEL_FORMAT_RGB565      UINT32_C(4)
""",
        """#define HVS_PIXEL_ORDER_XRGB         UINT32_C(2)
#define HVS_PIXEL_ORDER_XBGR         UINT32_C(3)
#define HVS_PIXEL_FORMAT_RGB332      UINT32_C(0)
#define HVS_PIXEL_FORMAT_RGBA4444    UINT32_C(1)
#define HVS_PIXEL_FORMAT_RGBA5551    UINT32_C(3)
#define HVS_PIXEL_FORMAT_RGB565      UINT32_C(4)
""",
    )
    replace_once(
        path,
        """    uint32_t tiling;
    uint32_t order;
    uint32_t format;
""",
        """    uint32_t tiling;
    uint32_t order;
    uint32_t expand;
    uint32_t format;
""",
    )
    replace_once(
        path,
        """    order = (ctl0 >> SCALER_CTL0_ORDER_SHIFT) &
            SCALER_CTL0_ORDER_MASK;
    format = ctl0 & SCALER_CTL0_FORMAT_MASK;
""",
        """    order = (ctl0 >> SCALER_CTL0_ORDER_SHIFT) &
            SCALER_CTL0_ORDER_MASK;
    expand = (ctl0 >> SCALER_CTL0_RGBA_EXPAND_SHIFT) &
             SCALER_CTL0_RGBA_EXPAND_MASK;
    format = ctl0 & SCALER_CTL0_FORMAT_MASK;
""",
    )
    replace_once(
        path,
        """    switch (format) {
    case HVS_PIXEL_FORMAT_RGB565:
        if (order != HVS_PIXEL_ORDER_XRGB &&
            order != HVS_PIXEL_ORDER_XBGR) {
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        pixo = order == HVS_PIXEL_ORDER_XBGR ?
               BCM2835_FB_PIXEL_ORDER_BGR :
               BCM2835_FB_PIXEL_ORDER_RGB;
        break;
    case HVS_PIXEL_FORMAT_RGB888:
""",
        """    switch (format) {
    case HVS_PIXEL_FORMAT_RGB332:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND ||
            (order != HVS_PIXEL_ORDER_ARGB &&
             order != HVS_PIXEL_ORDER_ABGR)) {
            return false;
        }
        bytes_per_pixel = sizeof(uint8_t);
        bits_per_pixel = 8;
        pixo = order == HVS_PIXEL_ORDER_ABGR ?
               BCM2835_FB_PIXEL_ORDER_HVS_BGR233 :
               BCM2835_FB_PIXEL_ORDER_HVS_RGB332;
        break;
    case HVS_PIXEL_FORMAT_RGBA4444:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND) {
            return false;
        }
        switch (order) {
        case HVS_PIXEL_ORDER_ABGR:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_XRGB4444;
            break;
        case HVS_PIXEL_ORDER_ARGB:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_XBGR4444;
            break;
        case HVS_PIXEL_ORDER_RGBA:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_BGRX4444;
            break;
        case HVS_PIXEL_ORDER_BGRA:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_RGBX4444;
            break;
        default:
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        break;
    case HVS_PIXEL_FORMAT_RGBA5551:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND ||
            order != HVS_PIXEL_ORDER_ABGR) {
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        pixo = BCM2835_FB_PIXEL_ORDER_HVS_XRGB1555;
        break;
    case HVS_PIXEL_FORMAT_RGB565:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND ||
            (order != HVS_PIXEL_ORDER_XRGB &&
             order != HVS_PIXEL_ORDER_XBGR)) {
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        pixo = order == HVS_PIXEL_ORDER_XBGR ?
               BCM2835_FB_PIXEL_ORDER_HVS_BGR565 :
               BCM2835_FB_PIXEL_ORDER_HVS_RGB565;
        break;
    case HVS_PIXEL_FORMAT_RGB888:
""",
    )


def rebase_rgb565_expectations() -> None:
    replace_once(
        Path("scripts/vc4/hvs-dlist-smoke.py"),
        """    expected_a = (
        (248, 0, 0),
        (0, 252, 0),
        (0, 0, 248),
        (248, 252, 248),
    )
    expected_b = (
        (248, 252, 248),
        (0, 0, 248),
        (0, 252, 0),
        (248, 0, 0),
    )
""",
        """    expected_a = (
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
""",
    )


def finalize_runtime_contract() -> None:
    packed_smoke = Path("scripts/vc4/hvs-packed-rgb-smoke.py")

    # The split staging patch accidentally qualified one locally defined
    # format constant through the imported support module.  Keep the format
    # vocabulary local to this focused test, as intended by the other packed
    # formats, and fail if the staging payload changes underneath us.
    replace_once(
        packed_smoke,
        "hvs.SCALER_PIXEL_FORMAT_RGB332",
        "HVS_PIXEL_FORMAT_RGB332",
    )

    # ROUND is endpoint-preserving and rounds intermediate components to the
    # nearest eight-bit value.  Use the same integer expression in the device
    # and in the independent expected-image generator.
    replace_once(
        Path("hw/display/bcm2835_fb.c"),
        "return value * UINT8_MAX / maximum;",
        "return (value * UINT8_MAX + maximum / 2) / maximum;",
    )
    replace_once(
        packed_smoke,
        "return value * 255 // maximum",
        "return (value * 255 + maximum // 2) // maximum",
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} PRODUCT_PATCH")
    patch = Path(sys.argv[1]).read_text(encoding="utf-8")

    write_new_from_patch(
        patch,
        ".github/workflows/vc4-hvs-packed-rgb.yml",
        0o644,
    )
    write_new_from_patch(
        patch,
        "scripts/vc4/hvs-packed-rgb-smoke.py",
        0o755,
    )
    replace_once(
        Path("scripts/vc4/hvs-packed-rgb-smoke.py"),
        """    # SCALER_CTL0_RGBA_EXPAND_ROUND repeats the high source bits into
    # the low destination bits.  Integer scaling by 255 / maximum is
    # exactly that bit-repetition mapping for the component widths here.
""",
        """    # Model the HVS ROUND path as an endpoint-preserving conversion
    # rounded to the nearest eight-bit component value.
""",
    )
    rebase_framebuffer_header()
    rebase_framebuffer_conversion()
    rebase_hvs_parser()
    rebase_rgb565_expectations()
    finalize_runtime_contract()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
