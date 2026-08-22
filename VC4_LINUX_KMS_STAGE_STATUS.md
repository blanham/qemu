# VC4 Linux KMS staged component frontier

- Source SHA: `15bf23db314625db0b19f504ce44b7500cadb27b`
- Full topology clear: `False`
- HDMI is the first staged regression: `True`

## Stage results

### render

- Frontier: **`drm-device-registered`**
- DRM registered: `True`
- KMS topology clear: `False`
- Bound components: `3f400000.hvs`/`vc4_hvs_ops`, `3f004000.txp`/`vc4_txp_ops`, `3fc00000.v3d`/`vc4_v3d_ops`

Relevant serial tail:
- `[    0.000000] Kernel command line: earlycon=pl011,0x3f201000 console=ttyAMA0,115200 rdinit=/init loglevel=8 ignore_loglevel printk.time=1 panic=-1 oops=panic random.trust_cpu=on`
- `[    0.000000] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 7645041785100000 ns`
- `[    0.000000] clocksource: arch_sys_counter: mask: 0x1ffffffffffffff max_cycles: 0x1cd42e208c, max_idle_ns: 881590405314 ns`
- `[    0.000099] sched_clock: 57 bits at 63MHz, resolution 16ns, wraps every 4398046511096ns`
- `[    0.643912] PTP clock support registered`
- `[    0.773854] clocksource: Switched to clocksource arch_sys_counter`
- `[    2.040293] brcmvirt-gpio soc:firmware:virtgpio: Failed to set gpiovirtbuf, trying to get err:0`
- `[    2.054158] brcmvirt-gpio soc:firmware:virtgpio: Failed to map physical address`
- `[    2.054490] brcmvirt-gpio soc:firmware:virtgpio: probe with driver brcmvirt-gpio failed with error -2`
- `[    3.122264] bcm2835-aux-uart 3f215040.serial: error -EINVAL: unable to register 8250 port`
- `[    3.124718] bcm2835-aux-uart 3f215040.serial: probe with driver bcm2835-aux-uart failed with error -22`
- `[    3.193233] clk: Disabling unused clocks`
- `[    5.029561] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[    5.036760] vc4-drm soc:gpu: bound 3f004000.txp (ops vc4_txp_ops [vc4])`
- `[    5.039282] vc4-drm soc:gpu: bound 3fc00000.v3d (ops vc4_v3d_ops [vc4])`
- `[    5.057362] vc4-drm soc:gpu: [drm] Cannot find any crtc or sizes`
- `[   13.621373] mmc1: Failed to initialize a non-removable card`
- `[   15.172107] platform leds: deferred probe pending: leds-gpio: Failed to get GPIO '/leds/led-act'`

### crtc

- Frontier: **`drm-device-registered`**
- DRM registered: `True`
- KMS topology clear: `False`
- Bound components: `3f400000.hvs`/`vc4_hvs_ops`, `3f004000.txp`/`vc4_txp_ops`, `3f206000.pixelvalve`/`vc4_crtc_ops`, `3f207000.pixelvalve`/`vc4_crtc_ops`, `3f807000.pixelvalve`/`vc4_crtc_ops`, `3fc00000.v3d`/`vc4_v3d_ops`

Relevant serial tail:
- `[    0.000000] Kernel command line: earlycon=pl011,0x3f201000 console=ttyAMA0,115200 rdinit=/init loglevel=8 ignore_loglevel printk.time=1 panic=-1 oops=panic random.trust_cpu=on`
- `[    0.000000] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 7645041785100000 ns`
- `[    0.000000] clocksource: arch_sys_counter: mask: 0x1ffffffffffffff max_cycles: 0x1cd42e208c, max_idle_ns: 881590405314 ns`
- `[    0.000122] sched_clock: 57 bits at 63MHz, resolution 16ns, wraps every 4398046511096ns`
- `[    0.573682] PTP clock support registered`
- `[    0.638487] clocksource: Switched to clocksource arch_sys_counter`
- `[    1.812350] brcmvirt-gpio soc:firmware:virtgpio: Failed to set gpiovirtbuf, trying to get err:0`
- `[    1.825421] brcmvirt-gpio soc:firmware:virtgpio: Failed to map physical address`
- `[    1.826074] brcmvirt-gpio soc:firmware:virtgpio: probe with driver brcmvirt-gpio failed with error -2`
- `[    2.650398] bcm2835-aux-uart 3f215040.serial: error -EINVAL: unable to register 8250 port`
- `[    2.651433] bcm2835-aux-uart 3f215040.serial: probe with driver bcm2835-aux-uart failed with error -22`
- `[    2.722450] clk: Disabling unused clocks`
- `[    5.024754] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[    5.031572] vc4-drm soc:gpu: bound 3f004000.txp (ops vc4_txp_ops [vc4])`
- `[    5.032496] vc4-drm soc:gpu: bound 3f206000.pixelvalve (ops vc4_crtc_ops [vc4])`
- `[    5.033179] vc4-drm soc:gpu: bound 3f207000.pixelvalve (ops vc4_crtc_ops [vc4])`
- `[    5.034188] vc4-drm soc:gpu: bound 3f807000.pixelvalve (ops vc4_crtc_ops [vc4])`
- `[    5.036409] vc4-drm soc:gpu: bound 3fc00000.v3d (ops vc4_v3d_ops [vc4])`
- `[    5.057452] vc4-drm soc:gpu: [drm] Cannot find any crtc or sizes`
- `[   15.139533] platform leds: deferred probe pending: leds-gpio: Failed to get GPIO '/leds/led-act'`

### hdmi

- Frontier: **`component-master-not-registered`**
- DRM registered: `False`
- KMS topology clear: `False`
- Bound components: `3f400000.hvs`/`vc4_hvs_ops`, `3f400000.hvs`/`vc4_hvs_ops`, `3f400000.hvs`/`vc4_hvs_ops`

Relevant serial tail:
- `[    0.000000] Kernel command line: earlycon=pl011,0x3f201000 console=ttyAMA0,115200 rdinit=/init loglevel=8 ignore_loglevel printk.time=1 panic=-1 oops=panic random.trust_cpu=on`
- `[    0.000000] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 7645041785100000 ns`
- `[    0.000000] clocksource: arch_sys_counter: mask: 0x1ffffffffffffff max_cycles: 0x1cd42e208c, max_idle_ns: 881590405314 ns`
- `[    0.000102] sched_clock: 57 bits at 63MHz, resolution 16ns, wraps every 4398046511096ns`
- `[    0.686045] PTP clock support registered`
- `[    0.762002] clocksource: Switched to clocksource arch_sys_counter`
- `[    1.946904] brcmvirt-gpio soc:firmware:virtgpio: Failed to set gpiovirtbuf, trying to get err:0`
- `[    1.961238] brcmvirt-gpio soc:firmware:virtgpio: Failed to map physical address`
- `[    1.961568] brcmvirt-gpio soc:firmware:virtgpio: probe with driver brcmvirt-gpio failed with error -2`
- `[    2.849274] bcm2835-aux-uart 3f215040.serial: error -EINVAL: unable to register 8250 port`
- `[    2.850378] bcm2835-aux-uart 3f215040.serial: probe with driver bcm2835-aux-uart failed with error -22`
- `[    3.016716] clk: Disabling unused clocks`
- `[    4.954235] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[    6.799033] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[   15.185750] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[   15.187227] platform leds: deferred probe pending: leds-gpio: Failed to get GPIO '/leds/led-act'`
- `[   15.187654] platform soc:gpu: deferred probe pending: (reason unknown)`
- `[   23.504898] mmc1: Failed to initialize a non-removable card`
- `[   27.411663] VC4_LINUX_KMS_FAILED stage=no-vc4-card`

### full

- Frontier: **`component-master-not-registered`**
- DRM registered: `False`
- KMS topology clear: `False`
- Bound components: `3f400000.hvs`/`vc4_hvs_ops`, `3f400000.hvs`/`vc4_hvs_ops`, `3f400000.hvs`/`vc4_hvs_ops`

Relevant serial tail:
- `[    0.000000] Kernel command line: earlycon=pl011,0x3f201000 console=ttyAMA0,115200 rdinit=/init loglevel=8 ignore_loglevel printk.time=1 panic=-1 oops=panic random.trust_cpu=on`
- `[    0.000000] clocksource: jiffies: mask: 0xffffffff max_cycles: 0xffffffff, max_idle_ns: 7645041785100000 ns`
- `[    0.000000] clocksource: arch_sys_counter: mask: 0x1ffffffffffffff max_cycles: 0x1cd42e208c, max_idle_ns: 881590405314 ns`
- `[    0.000113] sched_clock: 57 bits at 63MHz, resolution 16ns, wraps every 4398046511096ns`
- `[    0.588507] PTP clock support registered`
- `[    0.705785] clocksource: Switched to clocksource arch_sys_counter`
- `[    1.808158] brcmvirt-gpio soc:firmware:virtgpio: Failed to set gpiovirtbuf, trying to get err:0`
- `[    1.821499] brcmvirt-gpio soc:firmware:virtgpio: Failed to map physical address`
- `[    1.822150] brcmvirt-gpio soc:firmware:virtgpio: probe with driver brcmvirt-gpio failed with error -2`
- `[    2.718632] bcm2835-aux-uart 3f215040.serial: error -EINVAL: unable to register 8250 port`
- `[    2.719682] bcm2835-aux-uart 3f215040.serial: probe with driver bcm2835-aux-uart failed with error -22`
- `[    2.802784] clk: Disabling unused clocks`
- `[    5.706998] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[    6.557380] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[   12.975576] mmc1: Failed to initialize a non-removable card`
- `[   15.923650] vc4-drm soc:gpu: bound 3f400000.hvs (ops vc4_hvs_ops [vc4])`
- `[   15.925417] platform leds: deferred probe pending: leds-gpio: Failed to get GPIO '/leds/led-act'`
- `[   15.926169] platform soc:gpu: deferred probe pending: (reason unknown)`
- `[   27.827133] VC4_LINUX_KMS_FAILED stage=no-vc4-card`

