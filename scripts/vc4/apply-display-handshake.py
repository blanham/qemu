#!/usr/bin/env python3
"""Install the BCM2835 display handshake models on the VC4 KMS branch."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one integration anchor, found {count}"
        )
    file_path.write_text(text.replace(old, new, 1))


PIXELVALVE_C = '/*\n * BCM2835 pixel-valve timing and vblank interrupt model.\n *\n * The VC4 DRM driver completes synchronous atomic commits from the\n * pixel-valve VFP-start interrupt.  Model the register contract used by\n * Linux and schedule nominal 60 Hz VFP-start events while scanout is active.\n *\n * SPDX-License-Identifier: GPL-2.0-or-later\n */\n\n#include "qemu/osdep.h"\n#include "hw/display/bcm2835_pixelvalve.h"\n#include "hw/core/irq.h"\n#include "migration/vmstate.h"\n#include "qemu/log.h"\n#include "qemu/module.h"\n\n#define BCM2835_PIXELVALVE_FRAME_NS UINT64_C(16666667)\n\n#define PV_CONTROL_OFFSET           0x00\n#define PV_V_CONTROL_OFFSET         0x04\n#define PV_INTEN_OFFSET             0x24\n#define PV_INTSTAT_OFFSET           0x28\n\n#define PV_CONTROL_EN               BIT(0)\n#define PV_CONTROL_FIFO_CLR         BIT(1)\n#define PV_VCONTROL_VIDEN           BIT(0)\n#define PV_INT_VFP_START            BIT(7)\n\n#define REG_INDEX(offset) ((offset) >> 2)\n\nstatic bool bcm2835_pixelvalve_active(BCM2835PixelValveState *s)\n{\n    return (s->regs[REG_INDEX(PV_CONTROL_OFFSET)] & PV_CONTROL_EN) &&\n           (s->regs[REG_INDEX(PV_V_CONTROL_OFFSET)] & PV_VCONTROL_VIDEN);\n}\n\nstatic void bcm2835_pixelvalve_update_irq(BCM2835PixelValveState *s)\n{\n    uint32_t enabled = s->regs[REG_INDEX(PV_INTEN_OFFSET)];\n    uint32_t pending = s->regs[REG_INDEX(PV_INTSTAT_OFFSET)];\n\n    qemu_set_irq(s->irq, !!(enabled & pending));\n}\n\nstatic void bcm2835_pixelvalve_update_timer(BCM2835PixelValveState *s)\n{\n    if (bcm2835_pixelvalve_active(s)) {\n        if (!timer_pending(s->vblank_timer)) {\n            timer_mod(s->vblank_timer,\n                      qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +\n                      BCM2835_PIXELVALVE_FRAME_NS);\n        }\n    } else {\n        timer_del(s->vblank_timer);\n    }\n}\n\nstatic void bcm2835_pixelvalve_vblank(void *opaque)\n{\n    BCM2835PixelValveState *s = opaque;\n\n    if (!bcm2835_pixelvalve_active(s)) {\n        return;\n    }\n\n    s->regs[REG_INDEX(PV_INTSTAT_OFFSET)] |= PV_INT_VFP_START;\n    bcm2835_pixelvalve_update_irq(s);\n    timer_mod(s->vblank_timer,\n              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +\n              BCM2835_PIXELVALVE_FRAME_NS);\n}\n\nstatic uint64_t bcm2835_pixelvalve_read(void *opaque, hwaddr offset,\n                                        unsigned size)\n{\n    BCM2835PixelValveState *s = opaque;\n\n    if (offset >= BCM2835_PIXELVALVE_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_PIXELVALVE\n                      ": invalid read at 0x%" HWADDR_PRIx "\\n", offset);\n        return 0;\n    }\n\n    return s->regs[REG_INDEX(offset)];\n}\n\nstatic void bcm2835_pixelvalve_write(void *opaque, hwaddr offset,\n                                     uint64_t value, unsigned size)\n{\n    BCM2835PixelValveState *s = opaque;\n    uint32_t word = value;\n\n    if (offset >= BCM2835_PIXELVALVE_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_PIXELVALVE\n                      ": invalid write at 0x%" HWADDR_PRIx "\\n", offset);\n        return;\n    }\n\n    switch (offset) {\n    case PV_INTSTAT_OFFSET:\n        s->regs[REG_INDEX(offset)] &= ~word;\n        break;\n    case PV_CONTROL_OFFSET:\n        /*\n         * FIFO_CLR is a command pulse.  All other bits are retained.\n         * Linux disables EN before issuing the pulse and then programs\n         * the final control value in a later write.\n         */\n        s->regs[REG_INDEX(offset)] = word & ~PV_CONTROL_FIFO_CLR;\n        break;\n    default:\n        s->regs[REG_INDEX(offset)] = word;\n        break;\n    }\n\n    if (offset == PV_INTEN_OFFSET || offset == PV_INTSTAT_OFFSET) {\n        bcm2835_pixelvalve_update_irq(s);\n    }\n    if (offset == PV_CONTROL_OFFSET || offset == PV_V_CONTROL_OFFSET) {\n        bcm2835_pixelvalve_update_timer(s);\n    }\n}\n\nstatic const MemoryRegionOps bcm2835_pixelvalve_ops = {\n    .read = bcm2835_pixelvalve_read,\n    .write = bcm2835_pixelvalve_write,\n    .endianness = DEVICE_LITTLE_ENDIAN,\n    .valid = {\n        .min_access_size = 4,\n        .max_access_size = 4,\n        .unaligned = false,\n    },\n};\n\nstatic int bcm2835_pixelvalve_post_load(void *opaque, int version_id)\n{\n    BCM2835PixelValveState *s = opaque;\n\n    bcm2835_pixelvalve_update_irq(s);\n    bcm2835_pixelvalve_update_timer(s);\n    return 0;\n}\n\nstatic const VMStateDescription bcm2835_pixelvalve_vmstate = {\n    .name = TYPE_BCM2835_PIXELVALVE,\n    .version_id = 1,\n    .minimum_version_id = 1,\n    .post_load = bcm2835_pixelvalve_post_load,\n    .fields = (const VMStateField[]) {\n        VMSTATE_UINT32_ARRAY(regs, BCM2835PixelValveState,\n                             BCM2835_PIXELVALVE_REG_WORDS),\n        VMSTATE_TIMER_PTR(vblank_timer, BCM2835PixelValveState),\n        VMSTATE_END_OF_LIST()\n    }\n};\n\nstatic void bcm2835_pixelvalve_reset(DeviceState *dev)\n{\n    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);\n\n    memset(s->regs, 0, sizeof(s->regs));\n    timer_del(s->vblank_timer);\n    qemu_set_irq(s->irq, 0);\n}\n\nstatic void bcm2835_pixelvalve_realize(DeviceState *dev, Error **errp)\n{\n    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);\n\n    s->vblank_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,\n                                   bcm2835_pixelvalve_vblank, s);\n}\n\nstatic void bcm2835_pixelvalve_unrealize(DeviceState *dev)\n{\n    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);\n\n    timer_free(s->vblank_timer);\n    s->vblank_timer = NULL;\n}\n\nstatic void bcm2835_pixelvalve_init(Object *obj)\n{\n    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(obj);\n\n    memory_region_init_io(&s->iomem, obj, &bcm2835_pixelvalve_ops, s,\n                          TYPE_BCM2835_PIXELVALVE,\n                          BCM2835_PIXELVALVE_MMIO_SIZE);\n    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);\n    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);\n}\n\nstatic void bcm2835_pixelvalve_class_init(ObjectClass *klass,\n                                          const void *data)\n{\n    DeviceClass *dc = DEVICE_CLASS(klass);\n\n    dc->realize = bcm2835_pixelvalve_realize;\n    dc->unrealize = bcm2835_pixelvalve_unrealize;\n    dc->vmsd = &bcm2835_pixelvalve_vmstate;\n    device_class_set_legacy_reset(dc, bcm2835_pixelvalve_reset);\n}\n\nstatic const TypeInfo bcm2835_pixelvalve_info = {\n    .name = TYPE_BCM2835_PIXELVALVE,\n    .parent = TYPE_SYS_BUS_DEVICE,\n    .instance_size = sizeof(BCM2835PixelValveState),\n    .instance_init = bcm2835_pixelvalve_init,\n    .class_init = bcm2835_pixelvalve_class_init,\n};\n\nstatic void bcm2835_pixelvalve_register_types(void)\n{\n    type_register_static(&bcm2835_pixelvalve_info);\n}\n\ntype_init(bcm2835_pixelvalve_register_types)\n'
HVS_H = '/*\n * BCM2835 Hardware Video Scaler\n *\n * SPDX-License-Identifier: GPL-2.0-or-later\n */\n\n#ifndef HW_DISPLAY_BCM2835_HVS_H\n#define HW_DISPLAY_BCM2835_HVS_H\n\n#include "hw/core/sysbus.h"\n#include "qom/object.h"\n\n#define TYPE_BCM2835_HVS "bcm2835-hvs"\nOBJECT_DECLARE_SIMPLE_TYPE(BCM2835HVSState, BCM2835_HVS)\n\n#define BCM2835_HVS_MMIO_SIZE 0x6000\n#define BCM2835_HVS_REG_WORDS \\\n    (BCM2835_HVS_MMIO_SIZE / sizeof(uint32_t))\n\nstruct BCM2835HVSState {\n    SysBusDevice parent_obj;\n\n    MemoryRegion iomem;\n    qemu_irq irq;\n    uint32_t regs[BCM2835_HVS_REG_WORDS];\n};\n\n#endif /* HW_DISPLAY_BCM2835_HVS_H */\n'
HVS_C = '/*\n * BCM2835 Hardware Video Scaler register and display-list handoff model.\n *\n * The VC4 CRTC flip-completion path compares the software display-list\n * pointer against SCALER_DISPLACTx at VFP-start.  Retain the HVS register\n * aperture and model the pending-to-active display-list handoff so the\n * pixel-valve vblank interrupt can complete atomic commits.\n *\n * SPDX-License-Identifier: GPL-2.0-or-later\n */\n\n#include "qemu/osdep.h"\n#include "hw/display/bcm2835_hvs.h"\n#include "hw/core/irq.h"\n#include "migration/vmstate.h"\n#include "qemu/log.h"\n#include "qemu/module.h"\n\n#define SCALER_DISPCTRL               0x0000\n#define SCALER_DISPSTAT               0x0004\n#define SCALER_DISPLIST0              0x0020\n#define SCALER_DISPLACT0              0x0030\n#define SCALER_DISPCTRL0              0x0040\n#define SCALER_DISPSTAT0              0x0048\n\n#define SCALER_DISPCTRL_ENABLE        BIT(31)\n#define SCALER_DISPCTRLX_ENABLE       BIT(31)\n#define SCALER_DISPCTRLX_RESET        BIT(30)\n#define SCALER_DISPSTATX_MODE_RUN     (UINT32_C(2) << 30)\n#define SCALER_DISPSTATX_EMPTY        BIT(28)\n\n#define HVS_CHANNELS                  3\n#define REG_INDEX(offset) ((offset) >> 2)\n\nstatic const hwaddr hvs_displist[HVS_CHANNELS] = {\n    0x20, 0x24, 0x28,\n};\n\nstatic const hwaddr hvs_displact[HVS_CHANNELS] = {\n    0x30, 0x34, 0x38,\n};\n\nstatic const hwaddr hvs_dispctrl[HVS_CHANNELS] = {\n    0x40, 0x50, 0x60,\n};\n\nstatic const hwaddr hvs_dispstat[HVS_CHANNELS] = {\n    0x48, 0x58, 0x68,\n};\n\nstatic int bcm2835_hvs_find(const hwaddr *registers, hwaddr offset)\n{\n    unsigned int channel;\n\n    for (channel = 0; channel < HVS_CHANNELS; channel++) {\n        if (registers[channel] == offset) {\n            return channel;\n        }\n    }\n\n    return -1;\n}\n\nstatic uint64_t bcm2835_hvs_read(void *opaque, hwaddr offset, unsigned size)\n{\n    BCM2835HVSState *s = opaque;\n\n    if (offset >= BCM2835_HVS_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_HVS\n                      ": invalid read at 0x%" HWADDR_PRIx "\\n", offset);\n        return 0;\n    }\n\n    return s->regs[REG_INDEX(offset)];\n}\n\nstatic void bcm2835_hvs_write(void *opaque, hwaddr offset,\n                              uint64_t value, unsigned size)\n{\n    BCM2835HVSState *s = opaque;\n    uint32_t word = value;\n    int channel;\n\n    if (offset >= BCM2835_HVS_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_HVS\n                      ": invalid write at 0x%" HWADDR_PRIx "\\n", offset);\n        return;\n    }\n\n    if (offset == SCALER_DISPSTAT) {\n        s->regs[REG_INDEX(offset)] &= ~word;\n        return;\n    }\n\n    channel = bcm2835_hvs_find(hvs_displist, offset);\n    if (channel >= 0) {\n        s->regs[REG_INDEX(offset)] = word;\n        /*\n         * Real hardware changes DISPLACT at VSTART.  QEMU has no HVS\n         * compositor yet, so make the handoff visible immediately; the\n         * VC4 driver still consumes it only from its pixel-valve vblank\n         * handler.\n         */\n        s->regs[REG_INDEX(hvs_displact[channel])] = word;\n        return;\n    }\n\n    channel = bcm2835_hvs_find(hvs_dispctrl, offset);\n    if (channel >= 0) {\n        uint32_t control = word & ~SCALER_DISPCTRLX_RESET;\n\n        s->regs[REG_INDEX(offset)] = control;\n        if (control & SCALER_DISPCTRLX_ENABLE) {\n            s->regs[REG_INDEX(hvs_dispstat[channel])] =\n                SCALER_DISPSTATX_MODE_RUN;\n        } else {\n            s->regs[REG_INDEX(hvs_dispstat[channel])] =\n                SCALER_DISPSTATX_EMPTY;\n        }\n        return;\n    }\n\n    s->regs[REG_INDEX(offset)] = word;\n}\n\nstatic const MemoryRegionOps bcm2835_hvs_ops = {\n    .read = bcm2835_hvs_read,\n    .write = bcm2835_hvs_write,\n    .endianness = DEVICE_LITTLE_ENDIAN,\n    .valid = {\n        .min_access_size = 4,\n        .max_access_size = 4,\n        .unaligned = false,\n    },\n};\n\nstatic void bcm2835_hvs_reset(DeviceState *dev)\n{\n    BCM2835HVSState *s = BCM2835_HVS(dev);\n    unsigned int channel;\n\n    memset(s->regs, 0, sizeof(s->regs));\n    s->regs[REG_INDEX(SCALER_DISPCTRL)] = SCALER_DISPCTRL_ENABLE;\n    for (channel = 0; channel < HVS_CHANNELS; channel++) {\n        s->regs[REG_INDEX(hvs_dispstat[channel])] =\n            SCALER_DISPSTATX_EMPTY;\n    }\n    qemu_set_irq(s->irq, 0);\n}\n\nstatic int bcm2835_hvs_post_load(void *opaque, int version_id)\n{\n    BCM2835HVSState *s = opaque;\n\n    qemu_set_irq(s->irq, 0);\n    return 0;\n}\n\nstatic const VMStateDescription bcm2835_hvs_vmstate = {\n    .name = TYPE_BCM2835_HVS,\n    .version_id = 1,\n    .minimum_version_id = 1,\n    .post_load = bcm2835_hvs_post_load,\n    .fields = (const VMStateField[]) {\n        VMSTATE_UINT32_ARRAY(regs, BCM2835HVSState,\n                             BCM2835_HVS_REG_WORDS),\n        VMSTATE_END_OF_LIST()\n    }\n};\n\nstatic void bcm2835_hvs_init(Object *obj)\n{\n    BCM2835HVSState *s = BCM2835_HVS(obj);\n\n    memory_region_init_io(&s->iomem, obj, &bcm2835_hvs_ops, s,\n                          TYPE_BCM2835_HVS, BCM2835_HVS_MMIO_SIZE);\n    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);\n    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);\n}\n\nstatic void bcm2835_hvs_class_init(ObjectClass *klass, const void *data)\n{\n    DeviceClass *dc = DEVICE_CLASS(klass);\n\n    device_class_set_legacy_reset(dc, bcm2835_hvs_reset);\n    dc->vmsd = &bcm2835_hvs_vmstate;\n}\n\nstatic const TypeInfo bcm2835_hvs_info = {\n    .name = TYPE_BCM2835_HVS,\n    .parent = TYPE_SYS_BUS_DEVICE,\n    .instance_size = sizeof(BCM2835HVSState),\n    .instance_init = bcm2835_hvs_init,\n    .class_init = bcm2835_hvs_class_init,\n};\n\nstatic void bcm2835_hvs_register_types(void)\n{\n    type_register_static(&bcm2835_hvs_info);\n}\n\ntype_init(bcm2835_hvs_register_types)\n'
HDMI_C = '/*\n * BCM2835 HDMI controller register-handshake model.\n *\n * This slice retains the VC4 HDMI and HD register windows and implements\n * the synchronous handshakes polled by the Linux VC4 atomic-enable path:\n * scheduler HDMI-active, packet-RAM status, and FIFO recenter completion.\n *\n * SPDX-License-Identifier: GPL-2.0-or-later\n */\n\n#include "qemu/osdep.h"\n#include "hw/display/bcm2835_hdmi.h"\n#include "hw/core/irq.h"\n#include "migration/vmstate.h"\n#include "qemu/log.h"\n#include "qemu/module.h"\n\n#define HDMI_FIFO_CTL                         0x05c\n#define HDMI_RAM_PACKET_CONFIG                0x0a0\n#define HDMI_RAM_PACKET_STATUS                0x0a4\n#define HDMI_SCHEDULER_CONTROL                0x0c0\n\n#define HDMI_FIFO_CTL_RECENTER                BIT(6)\n#define HDMI_FIFO_CTL_RECENTER_DONE           BIT(14)\n#define HDMI_SCHEDULER_CONTROL_HDMI_ACTIVE    BIT(1)\n#define HDMI_SCHEDULER_CONTROL_MODE_HDMI      BIT(0)\n\n#define HDMI_PACKET_STATUS_MASK               UINT32_C(0xffff)\n#define REG_INDEX(offset) ((offset) >> 2)\n\nstatic uint64_t bcm2835_hdmi_core_read(void *opaque, hwaddr offset,\n                                       unsigned size)\n{\n    BCM2835HDMIState *s = opaque;\n\n    if (offset >= BCM2835_HDMI_CORE_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_HDMI\n                      ": invalid core read at 0x%" HWADDR_PRIx "\\n", offset);\n        return 0;\n    }\n\n    return s->core_regs[REG_INDEX(offset)];\n}\n\nstatic void bcm2835_hdmi_core_write(void *opaque, hwaddr offset,\n                                    uint64_t value, unsigned size)\n{\n    BCM2835HDMIState *s = opaque;\n    uint32_t word = value;\n\n    if (offset >= BCM2835_HDMI_CORE_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_HDMI\n                      ": invalid core write at 0x%" HWADDR_PRIx "\\n", offset);\n        return;\n    }\n\n    switch (offset) {\n    case HDMI_FIFO_CTL:\n        word &= ~HDMI_FIFO_CTL_RECENTER_DONE;\n        if (word & HDMI_FIFO_CTL_RECENTER) {\n            word |= HDMI_FIFO_CTL_RECENTER_DONE;\n        }\n        break;\n    case HDMI_RAM_PACKET_CONFIG:\n        s->core_regs[REG_INDEX(HDMI_RAM_PACKET_STATUS)] =\n            word & HDMI_PACKET_STATUS_MASK;\n        break;\n    case HDMI_SCHEDULER_CONTROL:\n        word &= ~HDMI_SCHEDULER_CONTROL_HDMI_ACTIVE;\n        if (word & HDMI_SCHEDULER_CONTROL_MODE_HDMI) {\n            word |= HDMI_SCHEDULER_CONTROL_HDMI_ACTIVE;\n        }\n        break;\n    default:\n        break;\n    }\n\n    s->core_regs[REG_INDEX(offset)] = word;\n}\n\nstatic uint64_t bcm2835_hdmi_hd_read(void *opaque, hwaddr offset,\n                                     unsigned size)\n{\n    BCM2835HDMIState *s = opaque;\n\n    if (offset >= BCM2835_HDMI_HD_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_HDMI\n                      ": invalid HD read at 0x%" HWADDR_PRIx "\\n", offset);\n        return 0;\n    }\n\n    return s->hd_regs[REG_INDEX(offset)];\n}\n\nstatic void bcm2835_hdmi_hd_write(void *opaque, hwaddr offset,\n                                  uint64_t value, unsigned size)\n{\n    BCM2835HDMIState *s = opaque;\n\n    if (offset >= BCM2835_HDMI_HD_MMIO_SIZE || (offset & 3)) {\n        qemu_log_mask(LOG_GUEST_ERROR,\n                      TYPE_BCM2835_HDMI\n                      ": invalid HD write at 0x%" HWADDR_PRIx "\\n", offset);\n        return;\n    }\n\n    s->hd_regs[REG_INDEX(offset)] = value;\n}\n\nstatic const MemoryRegionOps bcm2835_hdmi_core_ops = {\n    .read = bcm2835_hdmi_core_read,\n    .write = bcm2835_hdmi_core_write,\n    .endianness = DEVICE_LITTLE_ENDIAN,\n    .valid = {\n        .min_access_size = 4,\n        .max_access_size = 4,\n        .unaligned = false,\n    },\n};\n\nstatic const MemoryRegionOps bcm2835_hdmi_hd_ops = {\n    .read = bcm2835_hdmi_hd_read,\n    .write = bcm2835_hdmi_hd_write,\n    .endianness = DEVICE_LITTLE_ENDIAN,\n    .valid = {\n        .min_access_size = 4,\n        .max_access_size = 4,\n        .unaligned = false,\n    },\n};\n\nstatic void bcm2835_hdmi_reset(DeviceState *dev)\n{\n    BCM2835HDMIState *s = BCM2835_HDMI(dev);\n    unsigned int index;\n\n    memset(s->core_regs, 0, sizeof(s->core_regs));\n    memset(s->hd_regs, 0, sizeof(s->hd_regs));\n    for (index = 0; index < BCM2835_HDMI_IRQ_COUNT; index++) {\n        qemu_set_irq(s->irq[index], 0);\n    }\n}\n\nstatic int bcm2835_hdmi_post_load(void *opaque, int version_id)\n{\n    BCM2835HDMIState *s = opaque;\n    unsigned int index;\n\n    for (index = 0; index < BCM2835_HDMI_IRQ_COUNT; index++) {\n        qemu_set_irq(s->irq[index], 0);\n    }\n    return 0;\n}\n\nstatic const VMStateDescription bcm2835_hdmi_vmstate = {\n    .name = TYPE_BCM2835_HDMI,\n    .version_id = 1,\n    .minimum_version_id = 1,\n    .post_load = bcm2835_hdmi_post_load,\n    .fields = (const VMStateField[]) {\n        VMSTATE_UINT32_ARRAY(core_regs, BCM2835HDMIState,\n                             BCM2835_HDMI_CORE_REG_WORDS),\n        VMSTATE_UINT32_ARRAY(hd_regs, BCM2835HDMIState,\n                             BCM2835_HDMI_HD_REG_WORDS),\n        VMSTATE_END_OF_LIST()\n    }\n};\n\nstatic void bcm2835_hdmi_init(Object *obj)\n{\n    BCM2835HDMIState *s = BCM2835_HDMI(obj);\n    unsigned int index;\n\n    memory_region_init_io(&s->core_iomem, obj, &bcm2835_hdmi_core_ops, s,\n                          TYPE_BCM2835_HDMI "-core",\n                          BCM2835_HDMI_CORE_MMIO_SIZE);\n    memory_region_init_io(&s->hd_iomem, obj, &bcm2835_hdmi_hd_ops, s,\n                          TYPE_BCM2835_HDMI "-hd",\n                          BCM2835_HDMI_HD_MMIO_SIZE);\n    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->core_iomem);\n    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->hd_iomem);\n    for (index = 0; index < BCM2835_HDMI_IRQ_COUNT; index++) {\n        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[index]);\n    }\n}\n\nstatic void bcm2835_hdmi_class_init(ObjectClass *klass, const void *data)\n{\n    DeviceClass *dc = DEVICE_CLASS(klass);\n\n    device_class_set_legacy_reset(dc, bcm2835_hdmi_reset);\n    dc->vmsd = &bcm2835_hdmi_vmstate;\n}\n\nstatic const TypeInfo bcm2835_hdmi_info = {\n    .name = TYPE_BCM2835_HDMI,\n    .parent = TYPE_SYS_BUS_DEVICE,\n    .instance_size = sizeof(BCM2835HDMIState),\n    .instance_init = bcm2835_hdmi_init,\n    .class_init = bcm2835_hdmi_class_init,\n};\n\nstatic void bcm2835_hdmi_register_types(void)\n{\n    type_register_static(&bcm2835_hdmi_info);\n}\n\ntype_init(bcm2835_hdmi_register_types)\n'
DISPLAY_SMOKE = '#!/usr/bin/env python3\n"""Exercise BCM2835 HVS, HDMI, and pixel-valve display handshakes."""\n\nfrom __future__ import annotations\n\nimport argparse\nimport importlib.util\nfrom pathlib import Path\nimport subprocess\nimport tempfile\nfrom typing import Any\n\n\nRPI3_PERIPHERAL_BASE = 0x3F000000\nHVS_BASE = RPI3_PERIPHERAL_BASE + 0x00400000\nHDMI_CORE_BASE = RPI3_PERIPHERAL_BASE + 0x00902000\nPIXELVALVE_BASES = (\n    RPI3_PERIPHERAL_BASE + 0x00206000,\n    RPI3_PERIPHERAL_BASE + 0x00207000,\n    RPI3_PERIPHERAL_BASE + 0x00807000,\n)\nPIXELVALVE_IRQS = (45, 46, 42)\n\nIC_BASE = RPI3_PERIPHERAL_BASE + 0x0000B200\nIC_PENDING_2 = IC_BASE + 0x08\nIC_ENABLE_2 = IC_BASE + 0x14\nIC_DISABLE_2 = IC_BASE + 0x20\n\nSCALER_DISPCTRL = HVS_BASE + 0x00\nSCALER_DISPLIST = tuple(HVS_BASE + offset for offset in (0x20, 0x24, 0x28))\nSCALER_DISPLACT = tuple(HVS_BASE + offset for offset in (0x30, 0x34, 0x38))\nSCALER_DISPCTRLX = tuple(HVS_BASE + offset for offset in (0x40, 0x50, 0x60))\nSCALER_DISPSTATX = tuple(HVS_BASE + offset for offset in (0x48, 0x58, 0x68))\nSCALER_DISPCTRL_ENABLE = 1 << 31\nSCALER_DISPCTRLX_ENABLE = 1 << 31\nSCALER_DISPSTATX_MODE_MASK = 3 << 30\nSCALER_DISPSTATX_MODE_RUN = 2 << 30\n\nHDMI_FIFO_CTL = HDMI_CORE_BASE + 0x05C\nHDMI_RAM_PACKET_CONFIG = HDMI_CORE_BASE + 0x0A0\nHDMI_RAM_PACKET_STATUS = HDMI_CORE_BASE + 0x0A4\nHDMI_SCHEDULER_CONTROL = HDMI_CORE_BASE + 0x0C0\nHDMI_FIFO_CTL_RECENTER = 1 << 6\nHDMI_FIFO_CTL_RECENTER_DONE = 1 << 14\nHDMI_SCHEDULER_CONTROL_HDMI_ACTIVE = 1 << 1\nHDMI_SCHEDULER_CONTROL_MODE_HDMI = 1 << 0\n\nPV_CONTROL = 0x00\nPV_V_CONTROL = 0x04\nPV_INTEN = 0x24\nPV_INTSTAT = 0x28\nPV_CONTROL_EN = 1 << 0\nPV_CONTROL_FIFO_CLR = 1 << 1\nPV_INT_VFP_START = 1 << 7\nPV_VCONTROL_VIDEN = 1 << 0\nPV_VCONTROL_CONTINUOUS = 1 << 1\n\nFRAME_STEP_NS = 20_000_000\n\n\ndef load_property_support() -> Any:\n    support_path = Path(__file__).with_name("property-power-domain-smoke.py")\n    spec = importlib.util.spec_from_file_location(\n        "vc4_property_smoke_support", support_path\n    )\n    if spec is None or spec.loader is None:\n        raise RuntimeError(f"cannot load property smoke support: {support_path}")\n    module = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(module)\n    return module\n\n\ndef expect_bits(actual: int, required: int, description: str) -> None:\n    if actual & required != required:\n        raise RuntimeError(\n            f"{description}: 0x{actual:08x} lacks 0x{required:08x}"\n        )\n\n\ndef exercise_hvs(qtest: Any) -> None:\n    expect_bits(\n        qtest.readl(SCALER_DISPCTRL),\n        SCALER_DISPCTRL_ENABLE,\n        "HVS global enable reset state",\n    )\n\n    for channel in range(3):\n        display_list = 0x100 + channel * 0x40\n        qtest.writel(SCALER_DISPLIST[channel], display_list)\n        active = qtest.readl(SCALER_DISPLACT[channel])\n        if active != display_list:\n            raise RuntimeError(\n                f"HVS channel {channel} active list is 0x{active:08x}, "\n                f"expected 0x{display_list:08x}"\n            )\n\n        qtest.writel(SCALER_DISPCTRLX[channel], SCALER_DISPCTRLX_ENABLE)\n        status = qtest.readl(SCALER_DISPSTATX[channel])\n        if status & SCALER_DISPSTATX_MODE_MASK != SCALER_DISPSTATX_MODE_RUN:\n            raise RuntimeError(\n                f"HVS channel {channel} did not enter RUN: 0x{status:08x}"\n            )\n\n\ndef exercise_hdmi(qtest: Any) -> None:\n    if qtest.readl(HDMI_FIFO_CTL) != 0:\n        raise RuntimeError("HDMI FIFO control did not reset to zero")\n\n    qtest.writel(\n        HDMI_SCHEDULER_CONTROL,\n        HDMI_SCHEDULER_CONTROL_MODE_HDMI,\n    )\n    expect_bits(\n        qtest.readl(HDMI_SCHEDULER_CONTROL),\n        HDMI_SCHEDULER_CONTROL_MODE_HDMI\n        | HDMI_SCHEDULER_CONTROL_HDMI_ACTIVE,\n        "HDMI scheduler did not become active",\n    )\n    qtest.writel(HDMI_SCHEDULER_CONTROL, 0)\n    if qtest.readl(HDMI_SCHEDULER_CONTROL) & (\n        HDMI_SCHEDULER_CONTROL_MODE_HDMI\n        | HDMI_SCHEDULER_CONTROL_HDMI_ACTIVE\n    ):\n        raise RuntimeError("HDMI scheduler did not become inactive")\n\n    packet_mask = 0x15\n    qtest.writel(HDMI_RAM_PACKET_CONFIG, packet_mask)\n    if qtest.readl(HDMI_RAM_PACKET_STATUS) & 0xFFFF != packet_mask:\n        raise RuntimeError("HDMI packet-RAM status did not follow config")\n    qtest.writel(HDMI_RAM_PACKET_CONFIG, 0)\n    if qtest.readl(HDMI_RAM_PACKET_STATUS) & 0xFFFF:\n        raise RuntimeError("HDMI packet-RAM status did not clear")\n\n    qtest.writel(HDMI_FIFO_CTL, HDMI_FIFO_CTL_RECENTER)\n    fifo = qtest.readl(HDMI_FIFO_CTL)\n    expect_bits(\n        fifo,\n        HDMI_FIFO_CTL_RECENTER | HDMI_FIFO_CTL_RECENTER_DONE,\n        "HDMI FIFO recenter did not complete",\n    )\n\n    qtest.writel(HDMI_FIFO_CTL, 0)\n    fifo = qtest.readl(HDMI_FIFO_CTL)\n    if fifo & HDMI_FIFO_CTL_RECENTER_DONE:\n        raise RuntimeError(\n            f"HDMI FIFO recenter completion did not clear: 0x{fifo:08x}"\n        )\n\n\ndef exercise_pixelvalve(qtest: Any, base: int, irq: int, index: int) -> None:\n    irq_mask = 1 << (irq - 32)\n\n    if qtest.readl(base + PV_INTSTAT) != 0:\n        raise RuntimeError(f"pixel valve {index} interrupt pending after reset")\n\n    qtest.writel(IC_ENABLE_2, irq_mask)\n    qtest.writel(base + PV_INTEN, PV_INT_VFP_START)\n\n    # Neither half of the enable contract may start scanout on its own.\n    qtest.writel(base + PV_CONTROL, PV_CONTROL_EN)\n    qtest.command(f"clock_step {FRAME_STEP_NS}")\n    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:\n        raise RuntimeError(\n            f"pixel valve {index} ran without PV_VCONTROL_VIDEN"\n        )\n\n    qtest.writel(base + PV_CONTROL, PV_CONTROL_FIFO_CLR)\n    qtest.writel(\n        base + PV_V_CONTROL,\n        PV_VCONTROL_VIDEN | PV_VCONTROL_CONTINUOUS,\n    )\n    qtest.command(f"clock_step {FRAME_STEP_NS}")\n    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:\n        raise RuntimeError(\n            f"pixel valve {index} ran without PV_CONTROL_EN"\n        )\n\n    qtest.writel(base + PV_CONTROL, PV_CONTROL_EN)\n    qtest.command(f"clock_step {FRAME_STEP_NS}")\n    expect_bits(\n        qtest.readl(base + PV_INTSTAT),\n        PV_INT_VFP_START,\n        f"pixel valve {index} did not raise VFP-start",\n    )\n    expect_bits(\n        qtest.readl(IC_PENDING_2),\n        irq_mask,\n        f"pixel valve {index} did not route GPU IRQ {irq}",\n    )\n\n    qtest.writel(base + PV_INTSTAT, PV_INT_VFP_START)\n    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:\n        raise RuntimeError(\n            f"pixel valve {index} VFP-start was not cleared by W1C"\n        )\n    if qtest.readl(IC_PENDING_2) & irq_mask:\n        raise RuntimeError(\n            f"pixel valve {index} GPU IRQ {irq} did not deassert"\n        )\n\n    qtest.writel(base + PV_V_CONTROL, 0)\n    qtest.command(f"clock_step {FRAME_STEP_NS}")\n    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:\n        raise RuntimeError(\n            f"pixel valve {index} generated VFP-start while disabled"\n        )\n\n    qtest.writel(IC_DISABLE_2, irq_mask)\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser(description=__doc__)\n    parser.add_argument(\n        "--qemu",\n        type=Path,\n        default=Path("build/qemu-system-aarch64"),\n        help="path to qemu-system-aarch64",\n    )\n    args = parser.parse_args()\n\n    qemu = args.qemu.resolve()\n    if not qemu.is_file():\n        parser.error(f"QEMU binary does not exist: {qemu}")\n\n    support = load_property_support()\n\n    with tempfile.TemporaryDirectory(prefix="vc4-display-handshake-") as temp_dir:\n        temp = Path(temp_dir)\n        qtest_path = temp / "qtest.sock"\n        qmp_path = temp / "qmp.sock"\n        process = subprocess.Popen(\n            (\n                str(qemu),\n                "-M",\n                "raspi3b",\n                "-accel",\n                "qtest",\n                "-S",\n                "-display",\n                "none",\n                "-serial",\n                "none",\n                "-monitor",\n                "none",\n                "-qtest",\n                f"unix:{qtest_path},server=on,wait=off",\n                "-qmp",\n                f"unix:{qmp_path},server=on,wait=off",\n            ),\n            stdout=subprocess.DEVNULL,\n            stderr=subprocess.PIPE,\n            text=True,\n        )\n        qtest = None\n        qmp = None\n        try:\n            qtest = support.connect_when_ready(\n                qtest_path, process, support.QTestClient\n            )\n            qmp = support.connect_when_ready(\n                qmp_path, process, support.QMPClient\n            )\n\n            exercise_hvs(qtest)\n            exercise_hdmi(qtest)\n            for index, (base, irq) in enumerate(\n                zip(PIXELVALVE_BASES, PIXELVALVE_IRQS, strict=True)\n            ):\n                exercise_pixelvalve(qtest, base, irq, index)\n\n            qmp.execute("system_reset")\n            expect_bits(\n                qtest.readl(SCALER_DISPCTRL),\n                SCALER_DISPCTRL_ENABLE,\n                "HVS reset did not restore global enable",\n            )\n            if qtest.readl(HDMI_FIFO_CTL) != 0:\n                raise RuntimeError("HDMI FIFO state survived system reset")\n            for index, base in enumerate(PIXELVALVE_BASES):\n                if qtest.readl(base + PV_INTSTAT) != 0:\n                    raise RuntimeError(\n                        f"pixel valve {index} interrupt survived reset"\n                    )\n        finally:\n            if qmp is not None:\n                try:\n                    qmp.execute("quit")\n                except (OSError, RuntimeError):\n                    pass\n            if qtest is not None:\n                qtest.close()\n            if qmp is not None:\n                qmp.close()\n            try:\n                process.wait(timeout=5)\n            except subprocess.TimeoutExpired:\n                process.terminate()\n                try:\n                    process.wait(timeout=5)\n                except subprocess.TimeoutExpired:\n                    process.kill()\n                    process.wait(timeout=5)\n\n        if process.returncode not in (0, None):\n            stderr = process.stderr.read() if process.stderr else ""\n            raise RuntimeError(\n                f"QEMU exited with status {process.returncode}:\\n{stderr}"\n            )\n\n    print("BCM2835 display handshake smoke test passed")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'

Path("hw/display/bcm2835_pixelvalve.c").write_text(PIXELVALVE_C)
Path("include/hw/display/bcm2835_hvs.h").write_text(HVS_H)
Path("hw/display/bcm2835_hvs.c").write_text(HVS_C)
Path("hw/display/bcm2835_hdmi.c").write_text(HDMI_C)
Path("scripts/vc4/display-timing-smoke.py").write_text(DISPLAY_SMOKE)

replace_once(
    "hw/display/meson.build",
    "system_ss.add(when: 'CONFIG_RASPI', "
    "if_true: files('bcm2835_fb.c', 'bcm2835_v3d.c'))",
    dedent(
        """\
        system_ss.add(when: 'CONFIG_RASPI',
                      if_true: files('bcm2835_fb.c',
                                     'bcm2835_hdmi.c',
                                     'bcm2835_hvs.c',
                                     'bcm2835_pixelvalve.c',
                                     'bcm2835_v3d.c'))
        """
    ).rstrip(),
)

replace_once(
    "include/hw/arm/bcm2835_peripherals.h",
    '#include "hw/display/bcm2835_fb.h"\n'
    '#include "hw/display/bcm2835_v3d.h"\n',
    '#include "hw/display/bcm2835_fb.h"\n'
    '#include "hw/display/bcm2835_hdmi.h"\n'
    '#include "hw/display/bcm2835_hvs.h"\n'
    '#include "hw/display/bcm2835_pixelvalve.h"\n'
    '#include "hw/display/bcm2835_v3d.h"\n',
)
replace_once(
    "include/hw/arm/bcm2835_peripherals.h",
    "    BCM2835FBState fb;\n"
    "    BCM2835DMAState dma;\n",
    "    BCM2835FBState fb;\n"
    "    BCM2835HDMIState hdmi;\n"
    "    BCM2835HVSState hvs;\n"
    "    BCM2835PixelValveState pixelvalve[3];\n"
    "    BCM2835DMAState dma;\n",
)

replace_once(
    "include/hw/raspi/raspi_platform.h",
    "#define DBUS_OFFSET             0x900000\n"
    "#define AVE0_OFFSET             0x910000\n",
    "#define DBUS_OFFSET             0x900000\n"
    "#define HDMI_CORE_OFFSET        0x902000\n"
    "#define AVE0_OFFSET             0x910000\n",
)

replace_once(
    "hw/arm/bcm2835_peripherals.c",
    '    object_property_add_const_link(OBJECT(&s->fb), "dma-mr",\n'
    "                                   OBJECT(&s->gpu_bus_mr));\n"
    "\n"
    "    /* VideoCore IV 3D accelerator */\n",
    '    object_property_add_const_link(OBJECT(&s->fb), "dma-mr",\n'
    "                                   OBJECT(&s->gpu_bus_mr));\n"
    "\n"
    "    /* Native VC4 display pipeline timing devices. */\n"
    '    object_initialize_child(obj, "hdmi", &s->hdmi,\n'
    "                            TYPE_BCM2835_HDMI);\n"
    '    object_initialize_child(obj, "hvs", &s->hvs,\n'
    "                            TYPE_BCM2835_HVS);\n"
    '    object_initialize_child(obj, "pixelvalve0", &s->pixelvalve[0],\n'
    "                            TYPE_BCM2835_PIXELVALVE);\n"
    '    object_initialize_child(obj, "pixelvalve1", &s->pixelvalve[1],\n'
    "                            TYPE_BCM2835_PIXELVALVE);\n"
    '    object_initialize_child(obj, "pixelvalve2", &s->pixelvalve[2],\n'
    "                            TYPE_BCM2835_PIXELVALVE);\n"
    "\n"
    "    /* VideoCore IV 3D accelerator */\n",
)

replace_once(
    "hw/arm/bcm2835_peripherals.c",
    "    qdev_connect_gpio_out(DEVICE(&s->orgated_i2c_irq_splitter), 0,\n"
    "                          qdev_get_gpio_in_named(DEVICE(&s->ic),\n"
    "                                                 BCM2835_IC_GPU_IRQ,\n"
    "                                                 INTERRUPT_I2C));\n"
    "\n"
    "    /* VideoCore IV 3D accelerator */\n",
    "    qdev_connect_gpio_out(DEVICE(&s->orgated_i2c_irq_splitter), 0,\n"
    "                          qdev_get_gpio_in_named(DEVICE(&s->ic),\n"
    "                                                 BCM2835_IC_GPU_IRQ,\n"
    "                                                 INTERRUPT_I2C));\n"
    "\n"
    "    /* Hardware Video Scaler display-list handoff. */\n"
    "    if (!sysbus_realize(SYS_BUS_DEVICE(&s->hvs), errp)) {\n"
    "        return;\n"
    "    }\n"
    "    memory_region_add_subregion(\n"
    "        &s->peri_mr, HVS_OFFSET,\n"
    "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->hvs), 0));\n"
    "    sysbus_connect_irq(\n"
    "        SYS_BUS_DEVICE(&s->hvs), 0,\n"
    "        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,\n"
    "                               INTERRUPT_VIDEOSCALER));\n"
    "\n"
    "    /* HDMI core and HD register windows. */\n"
    "    if (!sysbus_realize(SYS_BUS_DEVICE(&s->hdmi), errp)) {\n"
    "        return;\n"
    "    }\n"
    "    memory_region_add_subregion(\n"
    "        &s->peri_mr, HDMI_CORE_OFFSET,\n"
    "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->hdmi), 0));\n"
    "    memory_region_add_subregion(\n"
    "        &s->peri_mr, HDMI_OFFSET,\n"
    "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->hdmi), 1));\n"
    "    sysbus_connect_irq(\n"
    "        SYS_BUS_DEVICE(&s->hdmi), 0,\n"
    "        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,\n"
    "                               INTERRUPT_HDMI0));\n"
    "    sysbus_connect_irq(\n"
    "        SYS_BUS_DEVICE(&s->hdmi), 1,\n"
    "        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,\n"
    "                               INTERRUPT_HDMI1));\n"
    "\n"
    "    /* Pixel valves 0, 1, and 2 drive the three VC4 CRTCs. */\n"
    "    for (n = 0; n < 3; n++) {\n"
    "        if (!sysbus_realize(SYS_BUS_DEVICE(&s->pixelvalve[n]), errp)) {\n"
    "            return;\n"
    "        }\n"
    "    }\n"
    "    memory_region_add_subregion(\n"
    "        &s->peri_mr, PIXV0_OFFSET,\n"
    "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->pixelvalve[0]), 0));\n"
    "    memory_region_add_subregion(\n"
    "        &s->peri_mr, PIXV1_OFFSET,\n"
    "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->pixelvalve[1]), 0));\n"
    "    memory_region_add_subregion(\n"
    "        &s->peri_mr, PIXV2_OFFSET,\n"
    "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->pixelvalve[2]), 0));\n"
    "    sysbus_connect_irq(\n"
    "        SYS_BUS_DEVICE(&s->pixelvalve[0]), 0,\n"
    "        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,\n"
    "                               INTERRUPT_PWA0));\n"
    "    sysbus_connect_irq(\n"
    "        SYS_BUS_DEVICE(&s->pixelvalve[1]), 0,\n"
    "        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,\n"
    "                               INTERRUPT_PWA1));\n"
    "    sysbus_connect_irq(\n"
    "        SYS_BUS_DEVICE(&s->pixelvalve[2]), 0,\n"
    "        qdev_get_gpio_in_named(DEVICE(&s->ic), BCM2835_IC_GPU_IRQ,\n"
    "                               INTERRUPT_PIXELVALVE1));\n"
    "\n"
    "    /* VideoCore IV 3D accelerator */\n",
)

Path("docs/system/arm/vc4-display-handshake.rst").write_text(
    dedent(
        """\
        VC4 display-pipeline handshakes
        =================================

        Native Raspberry Pi 3 KMS commits involve three independently visible
        hardware contracts in addition to V3D rendering:

        * the HVS changes ``SCALER_DISPLACTx`` to the newly installed display
          list;
        * the selected pixel valve raises ``PV_INT_VFP_START`` while both
          ``PV_CONTROL.EN`` and ``PV_V_CONTROL.VIDEN`` are asserted; and
        * HDMI reports scheduler-active, packet-RAM, and FIFO-recenter
          completion to the polling loops in the VC4 encoder driver.

        QEMU retains each register aperture and models those completion
        handshakes.  It does not yet compose HVS display lists into a host
        display surface; the existing V3D and firmware-framebuffer models
        remain separate.

        ``scripts/vc4/display-timing-smoke.py`` checks the HVS list handoff,
        all three pixel-valve interrupt routes, W1C interrupt status, enable
        gating, HDMI scheduler state, packet-RAM status, FIFO recentering, and
        reset behavior.
        """
    )
)
