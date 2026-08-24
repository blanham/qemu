#!/usr/bin/env python3
"""Keep the generated BCM2835 pixel-valve frame clock periodic.

The first display-timing witness deliberately advances time while only one
half of the PV enable contract is set.  The generated callback returned early
in that state without arming the next frame, so later enabling both halves had
no pending deadline.  Real pixel timing is continuous once the output clock is
available; model that as a periodic virtual frame clock and gate only the
VFP-start status/IRQ on the active scanout bits.
"""

from __future__ import annotations

from pathlib import Path


SOURCE = Path("hw/display/bcm2835_pixelvalve.c")

OLD_TIMER = '''static void bcm2835_pixelvalve_update_timer(BCM2835PixelValveState *s)
{
    if (bcm2835_pixelvalve_active(s)) {
        if (!timer_pending(s->vblank_timer)) {
            timer_mod(s->vblank_timer,
                      qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      BCM2835_PIXELVALVE_FRAME_NS);
        }
    } else {
        timer_del(s->vblank_timer);
    }
}
'''

NEW_TIMER = '''static void bcm2835_pixelvalve_schedule_frame(
    BCM2835PixelValveState *s)
{
    timer_mod(s->vblank_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              BCM2835_PIXELVALVE_FRAME_NS);
}

static void bcm2835_pixelvalve_update_timer(BCM2835PixelValveState *s)
{
    if (!timer_pending(s->vblank_timer)) {
        bcm2835_pixelvalve_schedule_frame(s);
    }
}
'''

OLD_CALLBACK = '''static void bcm2835_pixelvalve_vblank(void *opaque)
{
    BCM2835PixelValveState *s = opaque;

    if (!bcm2835_pixelvalve_active(s)) {
        return;
    }

    s->regs[REG_INDEX(PV_INTSTAT_OFFSET)] |= PV_INT_VFP_START;
    bcm2835_pixelvalve_update_irq(s);
    timer_mod(s->vblank_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              BCM2835_PIXELVALVE_FRAME_NS);
}
'''

NEW_CALLBACK = '''static void bcm2835_pixelvalve_vblank(void *opaque)
{
    BCM2835PixelValveState *s = opaque;

    if (bcm2835_pixelvalve_active(s)) {
        s->regs[REG_INDEX(PV_INTSTAT_OFFSET)] |= PV_INT_VFP_START;
        bcm2835_pixelvalve_update_irq(s);
    }
    bcm2835_pixelvalve_schedule_frame(s);
}
'''

OLD_RESET = '''    memset(s->regs, 0, sizeof(s->regs));
    timer_del(s->vblank_timer);
    qemu_set_irq(s->irq, 0);
'''

NEW_RESET = '''    memset(s->regs, 0, sizeof(s->regs));
    timer_del(s->vblank_timer);
    bcm2835_pixelvalve_schedule_frame(s);
    qemu_set_irq(s->irq, 0);
'''

OLD_REALIZE = '''    s->vblank_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                   bcm2835_pixelvalve_vblank, s);
'''

NEW_REALIZE = '''    s->vblank_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                   bcm2835_pixelvalve_vblank, s);
    bcm2835_pixelvalve_schedule_frame(s);
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = SOURCE.read_text()
    if NEW_TIMER not in text:
        text = replace_once(text, OLD_TIMER, NEW_TIMER, "timer policy")
    if NEW_CALLBACK not in text:
        text = replace_once(text, OLD_CALLBACK, NEW_CALLBACK, "vblank callback")
    if NEW_RESET not in text:
        text = replace_once(text, OLD_RESET, NEW_RESET, "reset scheduling")
    if NEW_REALIZE not in text:
        text = replace_once(text, OLD_REALIZE, NEW_REALIZE, "realize scheduling")

    stale = (
        OLD_TIMER in text or
        OLD_CALLBACK in text or
        OLD_RESET in text or
        OLD_REALIZE in text
    )
    if stale:
        raise SystemExit("stale one-shot pixel-valve timer logic remains")

    SOURCE.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
