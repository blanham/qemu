#!/usr/bin/env python3
"""GDB Python instrumentation for VideoCore IV CPU halt provenance.

This script is loaded by GDB, not executed by the host Python interpreter.
It installs a write watchpoint on the realized VC4 CPU's CPUState::halted
field and records the host stack for every transition.  Separate entry
breakpoints distinguish architectural BKPT/SLEEP, illegal-instruction, and
other direct CPU-core writes.
"""

from __future__ import annotations

import gdb


def eval_int(expression: str, default: int = -1) -> int:
    try:
        return int(gdb.parse_and_eval(expression))
    except gdb.error:
        return default


def prefixed_backtrace(prefix: str, depth: int = 40) -> None:
    try:
        text = gdb.execute(f"bt {depth}", to_string=True)
    except gdb.error as exc:
        print(f"{prefix} unavailable={exc}", flush=True)
        return

    for line in text.splitlines():
        print(f"{prefix} {line}", flush=True)


class VC4HaltedWatchpoint(gdb.Breakpoint):
    def __init__(self, cpu_address: int) -> None:
        self.cpu_address = cpu_address
        self.watch_expression = (
            f"((CPUState *)0x{cpu_address:x})->halted"
        )
        super().__init__(
            self.watch_expression,
            type=gdb.BP_WATCHPOINT,
            wp_class=gdb.WP_WRITE,
            internal=False,
        )
        self.silent = True
        print(
            "VC4_HALT_WATCH_INSTALLED "
            f"cpu=0x{cpu_address:x} expression={self.watch_expression}",
            flush=True,
        )

    def stop(self) -> bool:
        cpu = self.cpu_address
        halted = eval_int(f"((CPUState *)0x{cpu:x})->halted")
        cpu_index = eval_int(f"((CPUState *)0x{cpu:x})->cpu_index")
        exception_index = eval_int(
            f"((CPUState *)0x{cpu:x})->exception_index"
        )
        exit_request = eval_int(
            f"((CPUState *)0x{cpu:x})->exit_request"
        )
        interrupt_request = eval_int(
            f"((CPUState *)0x{cpu:x})->interrupt_request"
        )
        pc = eval_int(f"((VC4CPU *)0x{cpu:x})->env.pc")
        sr = eval_int(f"((VC4CPU *)0x{cpu:x})->env.sr")

        print(
            "VC4_HALTED_WRITE "
            f"cpu=0x{cpu:x} cpu-index={cpu_index} halted={halted} "
            f"pc=0x{pc & 0xffffffff:08x} sr=0x{sr & 0xffffffff:08x} "
            f"exception-index={exception_index} "
            f"exit-request={exit_request} "
            f"interrupt-request=0x{interrupt_request & 0xffffffff:08x}",
            flush=True,
        )
        prefixed_backtrace("VC4_HALTED_WRITE_BT")
        return False


class VC4RealizeBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__("vc4_cpu_realize", internal=False)
        self.silent = True
        self.watchpoint: VC4HaltedWatchpoint | None = None

    def stop(self) -> bool:
        # The diagnostic workflow runs on x86-64 Linux, where the first C
        # argument is in RDI.  vc4_cpu_realize() is only used for the VPU.
        cpu_address = eval_int("(uintptr_t)$rdi", 0)
        print(
            f"VC4_REALIZE_ENTRY cpu=0x{cpu_address:x}",
            flush=True,
        )
        if cpu_address:
            try:
                self.watchpoint = VC4HaltedWatchpoint(cpu_address)
            except gdb.error as exc:
                print(
                    "VC4_HALT_WATCH_INSTALL_FAILED "
                    f"cpu=0x{cpu_address:x} error={exc}",
                    flush=True,
                )
                raise
            self.enabled = False
        return False


class VC4HaltHelperBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__("helper_vc4_halt", internal=False)
        self.silent = True

    def stop(self) -> bool:
        env = eval_int("(uintptr_t)$rdi", 0)
        cpu = eval_int(
            "(uintptr_t)((char *)$rdi - sizeof(CPUState))",
            0,
        )
        pc = eval_int("((CPUVC4State *)$rdi)->pc")
        sr = eval_int("((CPUVC4State *)$rdi)->sr")
        cpu_index = (
            eval_int(f"((CPUState *)0x{cpu:x})->cpu_index")
            if cpu else -1
        )
        print(
            "VC4_HALT_HELPER_ENTRY "
            f"env=0x{env:x} cpu=0x{cpu:x} cpu-index={cpu_index} "
            f"pc=0x{pc & 0xffffffff:08x} sr=0x{sr & 0xffffffff:08x}",
            flush=True,
        )
        prefixed_backtrace("VC4_HALT_HELPER_BT")
        return False


class VC4IllegalHelperBreakpoint(gdb.Breakpoint):
    def __init__(self) -> None:
        super().__init__("helper_vc4_raise_illegal", internal=False)
        self.silent = True

    def stop(self) -> bool:
        env = eval_int("(uintptr_t)$rdi", 0)
        pc = eval_int("(uint32_t)$rsi")
        opcode = eval_int("(uint32_t)$rdx")
        print(
            "VC4_ILLEGAL_HELPER_ENTRY "
            f"env=0x{env:x} pc=0x{pc & 0xffffffff:08x} "
            f"opcode=0x{opcode & 0xffff:04x}",
            flush=True,
        )
        prefixed_backtrace("VC4_ILLEGAL_HELPER_BT")
        return False


VC4RealizeBreakpoint()
VC4HaltHelperBreakpoint()
VC4IllegalHelperBreakpoint()

print("VC4_HALT_PROVENANCE_READY", flush=True)
