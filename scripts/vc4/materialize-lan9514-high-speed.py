#!/usr/bin/env python3
"""Materialize the Pi 3B onboard LAN9514 high-speed hub model."""

from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one replacement site, found {count}"
        )
    return text.replace(old, new, 1)


def update_hub() -> None:
    path = Path("hw/usb/dev-hub.c")
    text = path.read_text(encoding="utf-8")
    if "static const USBDesc desc_hub_high" in text:
        print(f"{path}: high-speed hub model already present")
        return

    old_endpoint = """static const USBDescEndpoint desc_ep_hub = {
    .bEndpointAddress      = USB_DIR_IN | 0x01,
    .bmAttributes          = USB_ENDPOINT_XFER_INT,
    .wMaxPacketSize        = 1,
    .bInterval             = 0xff,
};
"""
    text = replace_once(
        text,
        old_endpoint,
        old_endpoint
        + """
static const USBDescEndpoint desc_ep_hub_high = {
    .bEndpointAddress      = USB_DIR_IN | 0x01,
    .bmAttributes          = USB_ENDPOINT_XFER_INT,
    .wMaxPacketSize        = 1,
    .bInterval             = 12,
};
""",
        path,
    )

    high_descriptors = """static const USBDescIface desc_iface_hub_high = {
    .bInterfaceNumber      = 0,
    .bNumEndpoints         = 1,
    .bInterfaceClass       = USB_CLASS_HUB,
    .bInterfaceSubClass    = 0,
    .bInterfaceProtocol    = 2,
    .eps = (const USBDescEndpoint[]) {
        desc_ep_hub_high,
    }
};

static const USBDescDevice desc_device_hub_high = {
    .bcdUSB                        = 0x0200,
    .bDeviceClass                  = USB_CLASS_HUB,
    .bDeviceSubClass               = 0,
    .bDeviceProtocol               = 2,
    .bMaxPacketSize0               = 64,
    .bNumConfigurations            = 1,
    .confs = (const USBDescConfig[]) {
        {
            .bNumInterfaces         = 1,
            .bConfigurationValue    = 1,
            .iConfiguration         = STR_CONFIG,
            .bmAttributes           = USB_CFG_ATT_SELFPOWER,
            .bMaxPower              = 0,
            .nif = 1,
            .ifs = &desc_iface_hub_high,
        },
    },
};

static const USBDesc desc_hub_high = {
    .id = {
        .idVendor          = 0x0409,
        .idProduct         = 0x55aa,
        .bcdDevice         = 0x0101,
        .iManufacturer     = STR_MANUFACTURER,
        .iProduct          = STR_PRODUCT,
        .iSerialNumber     = STR_SERIALNUMBER,
    },
    .full = &desc_device_hub,
    .high = &desc_device_hub_high,
    .str  = desc_strings,
};

"""
    marker = "static const USBDesc desc_hub = {\n"
    text = replace_once(text, marker, high_descriptors + marker, path)
    text = replace_once(
        text,
        "    uint32_t n_ports;\n",
        "    uint32_t n_ports;\n    bool high_speed;\n",
        path,
    )
    text = replace_once(
        text,
        "    int i;\n\n    usb_desc_create_serial(dev);\n",
        "    int i;\n"
        "    uint32_t speedmask;\n\n"
        "    if (s->high_speed) {\n"
        "        dev->usb_desc = &desc_hub_high;\n"
        "    }\n"
        "    usb_desc_create_serial(dev);\n",
        path,
    )
    text = replace_once(
        text,
        "    usb_desc_init(dev);\n"
        "    usb_ep_get(dev, USB_TOKEN_IN, 1);\n"
        "    QTAILQ_INIT(&s->ports);\n"
        "    for (i = 0; i < s->n_ports; i++) {\n",
        "    usb_desc_init(dev);\n"
        "    usb_ep_get(dev, USB_TOKEN_IN, 1);\n"
        "    speedmask = USB_SPEED_MASK_LOW | USB_SPEED_MASK_FULL;\n"
        "    if (s->high_speed) {\n"
        "        speedmask |= USB_SPEED_MASK_HIGH;\n"
        "    }\n"
        "    QTAILQ_INIT(&s->ports);\n"
        "    for (i = 0; i < s->n_ports; i++) {\n",
        path,
    )
    text = replace_once(
        text,
        "                          &usb_hub_port_ops,\n"
        "                          USB_SPEED_MASK_LOW | USB_SPEED_MASK_FULL);\n",
        "                          &usb_hub_port_ops, speedmask);\n",
        path,
    )
    text = replace_once(
        text,
        '    DEFINE_PROP_UINT32("ports", USBHubState, n_ports, 8),\n',
        '    DEFINE_PROP_UINT32("ports", USBHubState, n_ports, 8),\n'
        '    DEFINE_PROP_BOOL("high-speed", USBHubState, high_speed, false),\n',
        path,
    )
    path.write_text(text, encoding="utf-8")


def update_board() -> None:
    path = Path("hw/arm/vc4_raspi3_hetero.c")
    text = path.read_text(encoding="utf-8")
    if 'object_property_set_bool(OBJECT(hub), "high-speed"' in text:
        print(f"{path}: high-speed board property already present")
        return

    text = replace_once(
        text,
        '    object_property_set_int(OBJECT(hub), "ports",\n'
        "                            RASPI3_LAN9514_HUB_PORTS, &error_abort);\n"
        "    usb_realize_and_unref(hub, &ps->dwc2.bus, &error_fatal);\n",
        '    object_property_set_int(OBJECT(hub), "ports",\n'
        "                            RASPI3_LAN9514_HUB_PORTS, &error_abort);\n"
        '    object_property_set_bool(OBJECT(hub), "high-speed", true,\n'
        "                             &error_abort);\n"
        "    usb_realize_and_unref(hub, &ps->dwc2.bus, &error_fatal);\n",
        path,
    )
    path.write_text(text, encoding="utf-8")


def update_qtests() -> None:
    path = Path("tests/qtest/meson.build")
    text = path.read_text(encoding="utf-8")
    if "'bcm2835-usb-hub-test'" in text:
        print(f"{path}: USB hub qtest already registered")
        return

    text = replace_once(
        text,
        "    ['bcm2835-dma-test', 'bcm2835-i2c-test', "
        "'bcm2835-powermgt-test'] : []) +             \\\n",
        "    ['bcm2835-dma-test', 'bcm2835-i2c-test', "
        "'bcm2835-powermgt-test',                  \\\n"
        "     'bcm2835-usb-hub-test'] : []) +"
        "                                                   \\\n",
        path,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    update_hub()
    update_board()
    update_qtests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
