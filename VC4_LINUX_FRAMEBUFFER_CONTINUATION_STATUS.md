# VC4 Linux and framebuffer continuation status

Direct heterogeneous control passed: `True`

Stock-firmware Linux/framebuffer passed: `False`

Selected stock candidate: `None`

| Branch | Head | Linux/framebuffer | Subject |
|---|---|---:|---|
| `agent/vc4-hetero-direct-linux` | `294125dbf5e0` | `True` | ci/vc4: consolidate Linux and framebuffer evidence |
| `agent/vc4-linux-framebuffer-bringup` | `4cbf5d102868` | `True` | tests/vc4: record Linux framebuffer control proof |
| `agent/vc4-deterministic-time-linux` | `09532289dd43` | `False` | ci/vc4: compare stock-boot SDHOST candidates |

A hardware candidate is accepted only when the supplied ARM witness executes through stock firmware; VPU PC movement alone is diagnostic and is never treated as a pass.
