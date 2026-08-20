# VC4 V3D 2.1 register frontier

Guest driver: `drivers/gpu/drm/vc4/vc4_v3d.c`

This inventory is derived from the upstream Linux VC4 driver; it does not infer the contract from the newer `v3d` driver.

## First slice

The initial model is limited to power/reset visibility, identification, scratch/cache-control latches, interrupt state, and enough fault reporting for the Linux component bind probe.
Control-list execution remains a later, separately witnessed slice.

## Referenced registers

| Macro | Definition |
|---|---|
| `V3D_BFC` | `0x00134` |
| `V3D_BPCA` | `0x00300` |
| `V3D_BPCS` | `0x00304` |
| `V3D_BPOA` | `0x00308` |
| `V3D_BPOS` | `0x0030c` |
| `V3D_BXCF` | `0x00310` |
| `V3D_CT00RA0` | `0x00118` |
| `V3D_CT01RA0` | `0x0011c` |
| `V3D_CT0CA` | `0x00110` |
| `V3D_CT0CS` | `0x00100` |
| `V3D_CT0EA` | `0x00108` |
| `V3D_CT0LC` | `0x00120` |
| `V3D_CT0PC` | `0x00128` |
| `V3D_CT1CA` | `0x00114` |
| `V3D_CT1CS` | `0x00104` |
| `V3D_CT1EA` | `0x0010c` |
| `V3D_CT1LC` | `0x00124` |
| `V3D_CT1PC` | `0x0012c` |
| `V3D_DBGE` | `0x00f00` |
| `V3D_DRIVER_IRQS` | `indirect` |
| `V3D_ERRSTAT` | `0x00f20` |
| `V3D_EXPECTED_IDENT0` | `indirect` |
| `V3D_FDBGB` | `0x00f08` |
| `V3D_FDBGO` | `0x00f04` |
| `V3D_FDBGR` | `0x00f0c` |
| `V3D_FDBGS` | `0x00f10` |
| `V3D_IDENT0` | `0x00000` |
| `V3D_IDENT1` | `0x00004` |
| `V3D_IDENT1_NSEM` | `indirect` |
| `V3D_IDENT1_NSLC` | `indirect` |
| `V3D_IDENT1_QUPS` | `indirect` |
| `V3D_IDENT1_REV` | `indirect` |
| `V3D_IDENT1_TUPS` | `indirect` |
| `V3D_IDENT2` | `0x00008` |
| `V3D_INTCTL` | `0x00030` |
| `V3D_INTDIS` | `0x00038` |
| `V3D_INTENA` | `0x00034` |
| `V3D_INT_FLDONE` | `indirect` |
| `V3D_INT_FRDONE` | `indirect` |
| `V3D_INT_OUTOMEM` | `indirect` |
| `V3D_L2CACTL` | `0x00020` |
| `V3D_PCS` | `0x00130` |
| `V3D_PCTR` | `indirect` |
| `V3D_PCTRC` | `0x00670` |
| `V3D_PCTRE` | `0x00674` |
| `V3D_PCTRS` | `indirect` |
| `V3D_READ` | `indirect` |
| `V3D_RFC` | `0x00138` |
| `V3D_SCRATCH` | `0x00010` |
| `V3D_SLCACTL` | `0x00024` |
| `V3D_SQCNTL` | `0x00418` |
| `V3D_SQRSV0` | `0x00410` |
| `V3D_SQRSV1` | `0x00414` |
| `V3D_SRQCS` | `0x0043c` |
| `V3D_SRQPC` | `0x00430` |
| `V3D_SRQUA` | `0x00434` |
| `V3D_SRQUL` | `0x00438` |
| `V3D_VPACNTL` | `0x00500` |
| `V3D_VPMBASE` | `0x00504` |
| `V3D_WRITE` | `indirect` |

## Existing Linux-log hits

- `VC4_ARM_PAYLOAD_STATUS.json`: `          "qom_type": "vpu-vc4-cpu",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `      "info_cpus": "* CPU #0: thread_id=12919 model=cortex-a53\r\n  CPU #1: thread_id=12919 model=cortex-a53\r\n  CPU #2: thread_id=12919 model=cortex-a53\r\n  CPU #3: thread_id=12919 model=cortex-a53\r\n  CPU #4: thread_id=12919 model=vpu-vc4-cpu\r\n",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `          "qom-type": "vpu-vc4-cpu",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `    "image": "/tmp/vc4-stock-arm-payload/stock-arm-payload.img",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `      "raspi3b-vc4-hetero",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `      "file=/tmp/vc4-stock-arm-payload/stock-arm-payload.img,format=raw,if=sd",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `      "unix:/tmp/vc4-stock-arm-cxhdyiue/qmp.sock,server=on,wait=off",`
- `VC4_ARM_PAYLOAD_STATUS.json`: `      "unix:/tmp/vc4-stock-arm-cxhdyiue/qtest.sock,server=on,wait=off"`
- `VC4_POST_PREG_RECOVERY.json`: `  "ref_name": "agent/vc4-post-preg-recovery",`
- `VC4_POST_PREG_RECOVERY.json`: `          "qom_type": "vpu-vc4-cpu",`
- `VC4_POST_PREG_RECOVERY.json`: `      "info_cpus": "* CPU #0: thread_id=13162 model=cortex-a53\r\n  CPU #1: thread_id=13162 model=cortex-a53\r\n  CPU #2: thread_id=13162 model=cortex-a53\r\n  CPU #3: thread_id=13162 model=cortex-a53\r\n  CPU #4: thread_id=13162 model=vpu-vc4-cpu\r\n",`
- `VC4_POST_PREG_RECOVERY.json`: `          "qom-type": "vpu-vc4-cpu",`
- `VC4_POST_PREG_RECOVERY.json`: `    "image": "/tmp/vc4-post-preg-recovery/stock-arm-payload.img",`
- `VC4_POST_PREG_RECOVERY.json`: `      "raspi3b-vc4-hetero",`
- `VC4_POST_PREG_RECOVERY.json`: `      "file=/tmp/vc4-post-preg-recovery/stock-arm-payload.img,format=raw,if=sd",`
- `VC4_POST_PREG_RECOVERY.json`: `      "unix:/tmp/vc4-stock-arm-voou3b28/qmp.sock,server=on,wait=off",`
- `VC4_POST_PREG_RECOVERY.json`: `      "unix:/tmp/vc4-stock-arm-voou3b28/qtest.sock,server=on,wait=off"`
- `VC4_V3D_BRINGUP_STATUS.json`: `    "v3d": "success"`
- `VC4_V3D_BRINGUP_STATUS.json`: `  "v3d": {`
- `VC4_V3D_DIAGNOSTICS.json`: `      "  File \"/home/runner/work/qemu/qemu/scripts/vc4/v3d-integrate.py\", line 155, in <module>",`
- `VC4_V3D_DIAGNOSTICS.json`: `      "  File \"/home/runner/work/qemu/qemu/scripts/vc4/v3d-integrate.py\", line 149, in main",`
- `VC4_V3D_DIAGNOSTICS.json`: `      "  File \"/home/runner/work/qemu/qemu/scripts/vc4/v3d-integrate.py\", line 140, in update_peripherals",`
- `VC4_V3D_DIAGNOSTICS.json`: `      "    raise RuntimeError(\"obsolete unimplemented V3D placeholder remains\")",`
- `VC4_V3D_DIAGNOSTICS.json`: `      "RuntimeError: obsolete unimplemented V3D placeholder remains"`
- `VC4_V3D_DIAGNOSTICS.json`: `    "v3d": []`
- `VC4_V3D_DIAGNOSTICS.json`: `    "v3d": 125`
- `VC4_V3D_MATERIALIZE_STATUS.json`: `    "  File \"/home/runner/work/qemu/qemu/scripts/vc4/v3d-integrate.py\", line 155, in <module>",`
- `VC4_V3D_MATERIALIZE_STATUS.json`: `    "  File \"/home/runner/work/qemu/qemu/scripts/vc4/v3d-integrate.py\", line 149, in main",`
- `VC4_V3D_MATERIALIZE_STATUS.json`: `    "  File \"/home/runner/work/qemu/qemu/scripts/vc4/v3d-integrate.py\", line 140, in update_peripherals",`
- `VC4_V3D_MATERIALIZE_STATUS.json`: `    "    raise RuntimeError(\"obsolete unimplemented V3D placeholder remains\")",`
- `VC4_V3D_MATERIALIZE_STATUS.json`: `    "RuntimeError: obsolete unimplemented V3D placeholder remains"`
