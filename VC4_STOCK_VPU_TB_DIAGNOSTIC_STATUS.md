# VC4 stock-firmware VPU translation-block diagnostic

Classification: **`stock-vpu-delay-loop-progresses-in-both-tb-modes`**

| Measurement | Ordinary TCG | One instruction per TB |
|---|---:|---:|
| ARM witness | `False` | `False` |
| System timer delta (us) | `30031040` | `30027261` |
| Entered 0x540-0x54a loop | `True` | `True` |
| Post-entry samples | `598` | `598` |
| Post-entry states | `53` | `42` |
| Post-entry transitions | `52` | `41` |
| VPU timer register changed | `True` | `True` |
| Post-entry system timer delta (us) | `29915793` | `29911712` |
| Phase stationary | `False` | `False` |
| VPU debug-halted | `None` | `None` |
| VPU debug-stop | `None` | `None` |
| VPU debug-stopped | `None` | `None` |
| VPU hard interrupt | `None` | `None` |

The firmware, FAT image contents, machine, CPU count, and TCG thread mode are identical.  The only execution variable is the documented TCG `one-insn-per-tb` accelerator property.  No PC-specific behavior or firmware patch is used.
