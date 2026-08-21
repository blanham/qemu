#!/usr/bin/env python3
"""Apply the one-shot Raspberry Pi firmware power-domain implementation."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one replacement site, found {count}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        "include/hw/misc/bcm2835_property.h",
        """    uint32_t addr;
    char *command_line;
    bool pending;
""",
        """    uint32_t addr;
    uint32_t legacy_power_state;
    uint32_t power_domain_state;
    char *command_line;
    bool pending;
""",
    )

    replace_once(
        "hw/misc/bcm2835_property.c",
        """#define VCHI_BUSADDR_SIZE       sizeof(uint32_t)

/* https://github.com/raspberrypi/firmware/wiki/Mailbox-property-interface */
""",
        """#define VCHI_BUSADDR_SIZE       sizeof(uint32_t)
#define BCM2835_PROPERTY_POWER_STATE_COUNT 32

/* https://github.com/raspberrypi/firmware/wiki/Mailbox-property-interface */

static uint32_t bcm2835_property_get_power_state(uint32_t states,
                                                  uint32_t id)
{
    if (id >= BCM2835_PROPERTY_POWER_STATE_COUNT) {
        return 0;
    }

    return !!(states & (UINT32_C(1) << id));
}

static uint32_t bcm2835_property_set_power_state(uint32_t *states,
                                                  uint32_t id,
                                                  uint32_t requested)
{
    uint32_t mask;

    if (id >= BCM2835_PROPERTY_POWER_STATE_COUNT) {
        return 0;
    }

    mask = UINT32_C(1) << id;
    if (requested & 1) {
        *states |= mask;
    } else {
        *states &= ~mask;
    }

    return !!(*states & mask);
}
""",
    )

    replace_once(
        "hw/misc/bcm2835_property.c",
        """        case RPI_FWREQ_SET_POWER_STATE:
        {
            /*
             * Assume that whatever device they asked for exists,
             * and we'll just claim we set it to the desired state.
             */
            uint32_t state = ldl_le_phys(&s->dma_as, value + 16);
            stl_le_phys(&s->dma_as, value + 16, (state & 1));
            resplen = 8;
            break;
        }

        /* Clocks */
""",
        """        case RPI_FWREQ_GET_POWER_STATE:
        {
            uint32_t id = ldl_le_phys(&s->dma_as, value + 12);
            uint32_t state =
                bcm2835_property_get_power_state(s->legacy_power_state, id);

            stl_le_phys(&s->dma_as, value + 16, state);
            resplen = 8;
            break;
        }
        case RPI_FWREQ_SET_POWER_STATE:
        {
            uint32_t id = ldl_le_phys(&s->dma_as, value + 12);
            uint32_t requested = ldl_le_phys(&s->dma_as, value + 16);
            uint32_t state = bcm2835_property_set_power_state(
                &s->legacy_power_state, id, requested);

            stl_le_phys(&s->dma_as, value + 16, state);
            resplen = 8;
            break;
        }
        case RPI_FWREQ_GET_DOMAIN_STATE:
        {
            uint32_t id = ldl_le_phys(&s->dma_as, value + 12);
            uint32_t state =
                bcm2835_property_get_power_state(s->power_domain_state, id);

            stl_le_phys(&s->dma_as, value + 16, state);
            resplen = 8;
            break;
        }
        case RPI_FWREQ_SET_DOMAIN_STATE:
        {
            uint32_t id = ldl_le_phys(&s->dma_as, value + 12);
            uint32_t requested = ldl_le_phys(&s->dma_as, value + 16);
            uint32_t state = bcm2835_property_set_power_state(
                &s->power_domain_state, id, requested);

            stl_le_phys(&s->dma_as, value + 16, state);
            resplen = 8;
            break;
        }

        /* Clocks */
""",
    )

    replace_once(
        "hw/misc/bcm2835_property.c",
        """static const VMStateDescription vmstate_bcm2835_property = {
    .name = TYPE_BCM2835_PROPERTY,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_MACADDR(macaddr, BCM2835PropertyState),
        VMSTATE_UINT32(addr, BCM2835PropertyState),
        VMSTATE_BOOL(pending, BCM2835PropertyState),
        VMSTATE_END_OF_LIST()
    }
};
""",
        """static const VMStateDescription vmstate_bcm2835_property = {
    .name = TYPE_BCM2835_PROPERTY,
    .version_id = 2,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_MACADDR(macaddr, BCM2835PropertyState),
        VMSTATE_UINT32(addr, BCM2835PropertyState),
        VMSTATE_UINT32_V(legacy_power_state, BCM2835PropertyState, 2),
        VMSTATE_UINT32_V(power_domain_state, BCM2835PropertyState, 2),
        VMSTATE_BOOL(pending, BCM2835PropertyState),
        VMSTATE_END_OF_LIST()
    }
};
""",
    )

    replace_once(
        "hw/misc/bcm2835_property.c",
        """    s->pending = false;
}
""",
        """    s->pending = false;
    s->legacy_power_state = 0;
    s->power_domain_state = 0;
}
""",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
