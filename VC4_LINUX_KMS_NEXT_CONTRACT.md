# VC4 Linux KMS next hardware contract

Current frontier: **`vc4-component-bind-frontier`**

Native KMS topology is not yet clear, so a modeset witness would conflate object discovery with the remaining hardware contract. The machine-readable companion preserves the bound component set and the exact relevant kernel errors.

## Bound components

- `3f400000.hvs` via `vc4_hvs_ops`

## Relevant errors

- `[    2.636801] brcmvirt-gpio soc:firmware:virtgpio: Failed to set gpiovirtbuf, trying to get err:0`
- `[    2.649966] brcmvirt-gpio soc:firmware:virtgpio: Failed to map physical address`
- `[    2.650309] brcmvirt-gpio soc:firmware:virtgpio: probe with driver brcmvirt-gpio failed with error -2`
- `[    4.445284] bcm2835-aux-uart 3f215040.serial: error -EINVAL: unable to register 8250 port`
- `[    4.446701] bcm2835-aux-uart 3f215040.serial: probe with driver bcm2835-aux-uart failed with error -22`
- `[   14.783974] mmc1: Failed to initialize a non-removable card`
- `[   17.217346] platform leds: deferred probe pending: leds-gpio: Failed to get GPIO '/leds/led-act'`
- `[   17.218013] platform soc:gpu: deferred probe pending: (reason unknown)`
- `[   29.295154] VC4_LINUX_KMS_FAILED stage=no-vc4-card`
