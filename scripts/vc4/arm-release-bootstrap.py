#!/usr/bin/env python3
"""Materialize the VC4 -> AArch64 release regression machine.

This tranche deliberately uses a tiny, documented MMIO release block before
wiring the same callback into the BCM2837 power/reset model.  It proves the
hard execution property first: a VC4 TCG CPU can wake a held AArch64 TCG CPU
inside one qemu-system-aarch64 process.
"""

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]


def write(path: str, content: str) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(content).lstrip(), encoding="utf-8")


def insert_before(path: str, marker: str, addition: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    if marker not in text:
        raise SystemExit(f"expected marker not found in {path}: {marker!r}")
    p.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


write("hw/arm/vc4_arm_release_smoke.c", r'''
/*
 * VC4-controlled AArch64 release regression machine
 *
 * This is a deliberately small heterogeneous-TCG test fixture.  A VideoCore
 * IV VPU starts first, programs an ARM entry address through MMIO, and releases
 * a Cortex-A53 that was held powered off.  The device-facing contract is kept
 * separate from the eventual BCM2837 power-management wiring so the mixed-ISA
 * execution and wakeup semantics can be tested in isolation.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/module.h"
#include "qemu/units.h"
#include "hw/core/boards.h"
#include "hw/core/loader.h"
#include "hw/core/sysbus.h"
#include "system/cpus.h"
#include "system/memory.h"
#include "target/arm/cpu.h"

#define TYPE_VC4_ARM_RELEASE_MACHINE \
    MACHINE_TYPE_NAME("vc4-arm-release-smoke")
OBJECT_DECLARE_SIMPLE_TYPE(VC4ArmReleaseMachineState,
                           VC4_ARM_RELEASE_MACHINE)

#define VC4_ARM_RELEASE_BASE UINT64_C(0x10000000)
#define VC4_ARM_RELEASE_SIZE 0x1000

#define RELEASE_ENTRY_LO 0x00
#define RELEASE_ENTRY_HI 0x04
#define RELEASE_CONTROL  0x08
#define RELEASE_STATUS   0x0c
#define RELEASE_COUNT    0x10

#define RELEASE_CONTROL_GO  (1u << 0)
#define RELEASE_STATUS_DONE (1u << 0)

struct VC4ArmReleaseMachineState {
    MachineState parent_obj;

    MemoryRegion release_mr;
    CPUState *arm_cpu;
    CPUState *vc4_cpu;

    uint64_t arm_entry;
    uint32_t control;
    uint32_t status;
    uint32_t release_count;
};

static void vc4_arm_release_cpu(VC4ArmReleaseMachineState *s)
{
    CPUClass *cc;

    if (s->status & RELEASE_STATUS_DONE) {
        return;
    }
    if (!s->arm_cpu) {
        error_report("vc4-arm-release-smoke: no ARM CPU to release");
        return;
    }

    cc = CPU_GET_CLASS(s->arm_cpu);
    if (!cc->set_pc) {
        error_report("vc4-arm-release-smoke: ARM CPU has no set_pc hook");
        return;
    }

    /*
     * The CPU was realized with start_powered_off set.  Clear that policy
     * before reset so the common reset path leaves it runnable, then install
     * the VPU-selected entry point and kick its execution thread.
     */
    s->arm_cpu->start_powered_off = false;
    cpu_reset(s->arm_cpu);
    cc->set_pc(s->arm_cpu, s->arm_entry);
    s->arm_cpu->halted = 0;
    s->arm_cpu->stopped = false;
    s->arm_cpu->exception_index = -1;

    s->status |= RELEASE_STATUS_DONE;
    s->release_count++;
    qemu_cpu_kick(s->arm_cpu);
}

static uint64_t vc4_arm_release_read(void *opaque, hwaddr offset,
                                     unsigned size)
{
    VC4ArmReleaseMachineState *s = opaque;

    switch (offset) {
    case RELEASE_ENTRY_LO:
        return (uint32_t)s->arm_entry;
    case RELEASE_ENTRY_HI:
        return s->arm_entry >> 32;
    case RELEASE_CONTROL:
        return s->control;
    case RELEASE_STATUS:
        return s->status;
    case RELEASE_COUNT:
        return s->release_count;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "vc4-arm-release-smoke: bad read offset 0x%" HWADDR_PRIx
                      "\n", offset);
        return 0;
    }
}

static void vc4_arm_release_write(void *opaque, hwaddr offset,
                                  uint64_t value, unsigned size)
{
    VC4ArmReleaseMachineState *s = opaque;

    switch (offset) {
    case RELEASE_ENTRY_LO:
        s->arm_entry = deposit64(s->arm_entry, 0, 32, value);
        break;
    case RELEASE_ENTRY_HI:
        s->arm_entry = deposit64(s->arm_entry, 32, 32, value);
        break;
    case RELEASE_CONTROL:
        s->control = value;
        if (s->control & RELEASE_CONTROL_GO) {
            vc4_arm_release_cpu(s);
        }
        break;
    case RELEASE_STATUS:
        /* Write-one-to-clear is useful for later multi-release tests. */
        s->status &= ~value;
        break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "vc4-arm-release-smoke: bad write offset 0x%"
                      HWADDR_PRIx " value 0x%" PRIx64 "\n",
                      offset, value);
        break;
    }
}

static const MemoryRegionOps vc4_arm_release_ops = {
    .read = vc4_arm_release_read,
    .write = vc4_arm_release_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static CPUState *vc4_arm_release_new_vpu(void)
{
    /*
     * The secondary frontend keeps the native VPU model name when possible.
     * The additional candidates make this fixture tolerant of the temporary
     * names used by earlier versions of the development branch.
     */
    static const char * const candidates[] = {
        "vpu-vc4-cpu",
        "vpu-vc4-secondary-cpu",
        "vc4-vpu-secondary-cpu",
        "vc4-secondary-vpu-cpu",
    };
    ObjectClass *oc;
    Object *obj;
    size_t i;

    for (i = 0; i < ARRAY_SIZE(candidates); i++) {
        oc = object_class_by_name(candidates[i]);
        if (oc && object_class_dynamic_cast(oc, TYPE_CPU) &&
            !object_class_is_abstract(oc)) {
            obj = object_new(candidates[i]);
            return CPU(obj);
        }
    }

    error_report("vc4-arm-release-smoke: no linked VC4 VPU CPU type found");
    error_report("the AArch64 executable must include the secondary frontend");
    exit(EXIT_FAILURE);
}

static void vc4_arm_release_init(MachineState *machine)
{
    VC4ArmReleaseMachineState *s = VC4_ARM_RELEASE_MACHINE(machine);
    MemoryRegion *sysmem = get_system_memory();
    Object *arm_obj;
    CPUClass *vcc;
    ssize_t image_size;

    memory_region_add_subregion(sysmem, 0, machine->ram);

    memory_region_init_io(&s->release_mr, OBJECT(machine),
                          &vc4_arm_release_ops, s,
                          "vc4-arm-release", VC4_ARM_RELEASE_SIZE);
    memory_region_add_subregion(sysmem, VC4_ARM_RELEASE_BASE,
                                &s->release_mr);

    arm_obj = object_new(ARM_CPU_TYPE_NAME("cortex-a53"));
    s->arm_cpu = CPU(arm_obj);
    s->arm_cpu->start_powered_off = true;
    if (!qdev_realize(DEVICE(s->arm_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }

    s->vc4_cpu = vc4_arm_release_new_vpu();
    s->vc4_cpu->start_powered_off = false;
    if (!qdev_realize(DEVICE(s->vc4_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }

    vcc = CPU_GET_CLASS(s->vc4_cpu);
    g_assert(vcc->set_pc);
    vcc->set_pc(s->vc4_cpu, 0);

    if (!machine->kernel_filename) {
        error_report("vc4-arm-release-smoke requires -kernel IMAGE");
        exit(EXIT_FAILURE);
    }

    image_size = load_image_targphys(machine->kernel_filename, 0,
                                     machine->ram_size, NULL);
    if (image_size < 0) {
        error_report("could not load heterogeneous smoke image '%s'",
                     machine->kernel_filename);
        exit(EXIT_FAILURE);
    }
}

static void vc4_arm_release_machine_class_init(ObjectClass *oc,
                                                const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "VC4 firmware releases a held Cortex-A53 (TCG regression)";
    mc->init = vc4_arm_release_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a53");
    mc->default_ram_size = 16 * MiB;
    mc->default_ram_id = "vc4-arm-release-smoke.ram";
    mc->min_cpus = 2;
    mc->max_cpus = 2;
    mc->default_cpus = 2;
}

static const TypeInfo vc4_arm_release_machine_type = {
    .name = TYPE_VC4_ARM_RELEASE_MACHINE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(VC4ArmReleaseMachineState),
    .class_init = vc4_arm_release_machine_class_init,
};

static void vc4_arm_release_machine_register_types(void)
{
    type_register_static(&vc4_arm_release_machine_type);
}

type_init(vc4_arm_release_machine_register_types)
''')

write("scripts/vc4/arm-release-smoke.py", r'''
#!/usr/bin/env python3
"""Execute a VC4 -> Cortex-A53 release transaction under one TCG process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any

RELEASE_BASE = 0x10000000
ARM_ENTRY = 0x1000
MARKER_ADDR = 0x2000
MARKER_VALUE = 0xA55A5AA5

MOV = 0
LSL = 28


def half(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def word(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def vc4_alu_imm16(op: int, rd: int, value: int) -> bytes:
    return half(0xB000 | ((op & 0x1F) << 5) | (rd & 0x1F)) + half(value)


def vc4_mov(rd: int, value: int) -> bytes:
    return vc4_alu_imm16(MOV, rd, value)


def vc4_small_imm(op: int, rd: int, value: int) -> bytes:
    if op & 1 or rd >= 16 or not 0 <= value < 32:
        raise ValueError("invalid VC4 short immediate")
    return half(0x6000 | ((op // 2) << 9) | ((value & 0x1F) << 4) | rd)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | ((fmt & 3) << 6) | (rd & 0x1F)
    if raw & 0x800:
        i1 |= 0x100
    i2 = ((rb & 0x1F) << 11) | (raw & 0x7FF)
    return half(i1) + half(i2)


def a64_movz(rd: int, imm16: int, shift: int = 0, *, sf: bool = True) -> int:
    base = 0xD2800000 if sf else 0x52800000
    return base | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def a64_movk(rd: int, imm16: int, shift: int = 0, *, sf: bool = True) -> int:
    base = 0xF2800000 if sf else 0x72800000
    return base | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def build_image(path: Path) -> None:
    vc4 = bytearray()
    vc4 += vc4_mov(0, 0x1000)
    vc4 += vc4_small_imm(LSL, 0, 16)          # r0 = 0x10000000
    vc4 += vc4_mov(1, ARM_ENTRY)
    vc4 += vc4_memory_offset(True, 1, 0, 0)   # ENTRY_LO
    vc4 += vc4_mov(1, 1)
    vc4 += vc4_memory_offset(True, 1, 0, 8)   # CONTROL.GO
    vc4 += half(0x0000)                       # development HALT

    arm = b"".join([
        word(a64_movz(0, MARKER_ADDR, sf=True)),
        word(a64_movz(1, MARKER_VALUE & 0xFFFF, sf=False)),
        word(a64_movk(1, MARKER_VALUE >> 16, shift=16, sf=False)),
        word(0xB9000001),                     # str w1, [x0]
        word(0x14000000),                     # b .
    ])

    image = bytearray(ARM_ENTRY + len(arm))
    image[:len(vc4)] = vc4
    image[ARM_ENTRY:ARM_ENTRY + len(arm)] = arm
    path.write_bytes(image)


class LineSocket:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def send_line(self, line: str) -> str:
        self.file.write(line.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"socket closed while waiting for {line!r}")
        return reply.decode("ascii", errors="replace").strip()

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QMP:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"invalid QMP greeting: {greeting}")
        self.execute("qmp_capabilities")

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP socket closed")
            msg = json.loads(line)
            if "event" not in msg:
                return msg

    def execute(self, command: str) -> Any:
        payload = json.dumps({"execute": command}).encode("utf-8") + b"\n"
        self.file.write(payload)
        msg = self._read_message()
        if "error" in msg:
            raise RuntimeError(f"QMP {command} failed: {msg['error']}")
        return msg.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_socket(path: Path, proc: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        time.sleep(0.01)
    raise TimeoutError(f"socket did not appear: {path}")


def parse_qtest_value(reply: str) -> int:
    parts = reply.split()
    if len(parts) != 2 or parts[0] != "OK":
        raise RuntimeError(f"unexpected qtest reply: {reply!r}")
    return int(parts[1], 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-arm-release-") as tmp_s:
        tmp = Path(tmp_s)
        image = tmp / "release.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        build_image(image)

        cmd = [
            str(qemu),
            "-M", "vc4-arm-release-smoke",
            "-m", "16M",
            "-kernel", str(image),
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-S",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr)

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        try:
            wait_for_socket(qmp_path, proc, 5.0)
            wait_for_socket(qtest_path, proc, 5.0)
            qmp = QMP(qmp_path)
            qtest = LineSocket(qtest_path)

            cpus = qmp.execute("query-cpus-fast")
            if not isinstance(cpus, list) or len(cpus) != 2:
                raise RuntimeError(f"expected two heterogeneous CPUs, got {cpus!r}")

            qmp.execute("cont")
            deadline = time.monotonic() + 5.0
            marker = 0
            while time.monotonic() < deadline:
                marker = parse_qtest_value(qtest.send_line(f"readl 0x{MARKER_ADDR:x}"))
                if marker == MARKER_VALUE:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"QEMU exited with status {proc.returncode}")
                time.sleep(0.01)

            if marker != MARKER_VALUE:
                raise RuntimeError(
                    f"ARM marker never appeared: got 0x{marker:08x}, "
                    f"expected 0x{MARKER_VALUE:08x}"
                )

            status = parse_qtest_value(
                qtest.send_line(f"readl 0x{RELEASE_BASE + 0x0c:x}")
            )
            count = parse_qtest_value(
                qtest.send_line(f"readl 0x{RELEASE_BASE + 0x10:x}")
            )
            if status & 1 == 0 or count != 1:
                raise RuntimeError(
                    f"release device state is wrong: status=0x{status:08x} count={count}"
                )

            print(
                "VC4 -> ARM release passed: "
                f"cpus={len(cpus)} marker=0x{marker:08x} "
                f"status=0x{status:08x} releases={count}"
            )
            qmp.execute("quit")
            proc.wait(timeout=5)
            return 0
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            diagnostics = stderr_path.read_text(encoding="utf-8", errors="replace")
            if diagnostics:
                print("--- qemu stderr ---", file=os.sys.stderr)
                print(diagnostics, file=os.sys.stderr)
            raise
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()


if __name__ == "__main__":
    raise SystemExit(main())
''')

insert_before(
    "hw/arm/meson.build",
    "\nhw_common_arch += {'arm': arm_common_ss}",
    "\n# Heterogeneous TCG regression: VC4 releases a powered-off Cortex-A53.\n"
    "arm_common_ss.add(when: 'TARGET_AARCH64',\n"
    "                  if_true: files('vc4_arm_release_smoke.c'))\n",
)
