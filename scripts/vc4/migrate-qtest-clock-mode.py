#!/usr/bin/env python3
"""Make VC4 stock probes tolerate qtest control sockets under TCG.

QEMU exposes qtest memory access whenever a qtest chardev is configured, but
clock_step/clock_set are deliberately available only when the qtest accelerator
is selected.  The heterogeneous VC4 machine must run TCG, so the probes should
try clock_step once, recognize the unsupported-command response, and then let
TCG advance QEMU_CLOCK_VIRTUAL normally.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one replacement anchor, found {count}"
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def update_arm_payload() -> None:
    path = "scripts/vc4/raspi3-stock-arm-payload.py"
    replace_once(
        path,
        """The qtest socket is retained for race-free physical-memory inspection.  QEMU's
qtest protocol makes the client completely responsible for QEMU_CLOCK_VIRTUAL,
so the probe advances virtual time explicitly while the firmware runs.  Without
that step, ordinary firmware delay loops appear to hang even though the CPU and
device implementations are correct.
""",
        """The qtest socket is retained for race-free physical-memory inspection.  Clock
management commands are available only with QEMU's qtest accelerator.  This
probe runs the heterogeneous machine under TCG, so it detects that boundary
once and otherwise lets TCG advance QEMU_CLOCK_VIRTUAL normally.
""",
    )
    replace_once(
        path,
        """def advance_clock(qtest: LineSocket, nanoseconds: int) -> int:
    if nanoseconds <= 0:
        raise ValueError("qtest clock step must be positive")
    return parse_qtest_value(qtest.send_line(f"clock_step {nanoseconds}"))
""",
        """def try_advance_clock(qtest: LineSocket,
                      nanoseconds: int) -> int | None:
    if nanoseconds <= 0:
        raise ValueError("qtest clock step must be positive")
    reply = qtest.send_line(f"clock_step {nanoseconds}")
    if reply.startswith("OK "):
        return parse_qtest_value(reply)
    if reply.startswith("FAIL Unknown command"):
        return None
    raise RuntimeError(f"unexpected qtest clock reply: {reply!r}")
""",
    )
    replace_once(
        path,
        """        help=(
            "virtual nanoseconds advanced before each signature poll; "
            f"default: {DEFAULT_CLOCK_STEP_NS}"
        ),
""",
        """        help=(
            "virtual nanoseconds requested when qtest clock management is "
            "available; TCG otherwise advances time normally; "
            f"default: {DEFAULT_CLOCK_STEP_NS}"
        ),
""",
    )
    replace_once(
        path,
        """            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
""",
        """            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
            "qtest_clock_step_supported": None,
            "clock_mode": "probing",
""",
    )
    replace_once(
        path,
        """            qtest_clock_ns = 0
            while time.monotonic() < deadline:
                qtest_clock_ns = advance_clock(qtest, args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                signature = readq(qtest, SIGNATURE_ADDR)
""",
        """            qtest_clock_ns: int | None = None
            clock_step_available = True
            while time.monotonic() < deadline:
                if clock_step_available:
                    stepped = try_advance_clock(qtest, args.clock_step_ns)
                    if stepped is None:
                        clock_step_available = False
                        result["qtest_clock_step_supported"] = False
                        result["clock_mode"] = "tcg-realtime"
                    else:
                        qtest_clock_ns = stepped
                        result["qtest_clock_steps"] += 1
                        result["qtest_clock_step_supported"] = True
                        result["clock_mode"] = "qtest-controlled"
                signature = readq(qtest, SIGNATURE_ADDR)
""",
    )


def qtest_class(old_has_readq: bool) -> tuple[str, str]:
    readq = """
    def readq(self, address: int) -> int:
        fields = self.command(f"readq 0x{address:x}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)
""" if old_has_readq else ""
    old = f"""    def command(self, command: str) -> list[str]:
        self.file.write(command.encode("ascii") + b"\\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest closed during {{command!r}}")
        fields = reply.decode("ascii", errors="replace").strip().split()
        if not fields or fields[0] != "OK":
            raise RuntimeError(f"qtest rejected {{command!r}}: {{fields!r}}")
        return fields

    def readl(self, address: int) -> int:
        fields = self.command(f"readl 0x{{address:x}}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {{fields!r}}")
        return int(fields[1], 0)
{readq}
    def advance(self, nanoseconds: int) -> int:
        fields = self.command(f"clock_step {{nanoseconds}}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest clock reply: {{fields!r}}")
        return int(fields[1], 0)
"""
    new = f"""    def raw_command(self, command: str) -> str:
        self.file.write(command.encode("ascii") + b"\\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest closed during {{command!r}}")
        return reply.decode("ascii", errors="replace").strip()

    def command(self, command: str) -> list[str]:
        fields = self.raw_command(command).split()
        if not fields or fields[0] != "OK":
            raise RuntimeError(f"qtest rejected {{command!r}}: {{fields!r}}")
        return fields

    def readl(self, address: int) -> int:
        fields = self.command(f"readl 0x{{address:x}}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {{fields!r}}")
        return int(fields[1], 0)
{readq}
    def try_advance(self, nanoseconds: int) -> int | None:
        fields = self.raw_command(f"clock_step {{nanoseconds}}").split()
        if len(fields) == 2 and fields[0] == "OK":
            return int(fields[1], 0)
        if fields[:3] == ["FAIL", "Unknown", "command"]:
            return None
        raise RuntimeError(f"malformed qtest clock reply: {{fields!r}}")
"""
    return old, new


def update_framebuffer() -> None:
    path = "scripts/vc4/raspi3-stock-framebuffer.py"
    old, new = qtest_class(True)
    replace_once(path, old, new)
    replace_once(
        path,
        """            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
""",
        """            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
            "qtest_clock_step_supported": None,
            "clock_mode": "probing",
""",
    )
    replace_once(
        path,
        """            qtest_clock_ns = 0
            while time.monotonic() < deadline:
                qtest_clock_ns = qtest.advance(args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                magic = qtest.readq(RESULT_ADDRESS)
""",
        """            qtest_clock_ns: int | None = None
            clock_step_available = True
            while time.monotonic() < deadline:
                if clock_step_available:
                    stepped = qtest.try_advance(args.clock_step_ns)
                    if stepped is None:
                        clock_step_available = False
                        result["qtest_clock_step_supported"] = False
                        result["clock_mode"] = "tcg-realtime"
                    else:
                        qtest_clock_ns = stepped
                        result["qtest_clock_steps"] += 1
                        result["qtest_clock_step_supported"] = True
                        result["clock_mode"] = "qtest-controlled"
                magic = qtest.readq(RESULT_ADDRESS)
""",
    )


def update_linux() -> None:
    path = "scripts/vc4/raspi3-stock-linux.py"
    old, new = qtest_class(False)
    replace_once(path, old, new)
    replace_once(
        path,
        """            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
""",
        """            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
            "qtest_clock_step_supported": None,
            "clock_mode": "probing",
""",
    )
    replace_once(
        path,
        """            qtest_clock_ns = 0
            text = ""
            while time.monotonic() < deadline:
                qtest_clock_ns = qtest.advance(args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                text = serial_text(serial_path)
""",
        """            qtest_clock_ns: int | None = None
            clock_step_available = True
            text = ""
            while time.monotonic() < deadline:
                if clock_step_available:
                    stepped = qtest.try_advance(args.clock_step_ns)
                    if stepped is None:
                        clock_step_available = False
                        result["qtest_clock_step_supported"] = False
                        result["clock_mode"] = "tcg-realtime"
                    else:
                        qtest_clock_ns = stepped
                        result["qtest_clock_steps"] += 1
                        result["qtest_clock_step_supported"] = True
                        result["clock_mode"] = "qtest-controlled"
                text = serial_text(serial_path)
""",
    )


def update_workflow() -> None:
    path = ".github/workflows/vc4-linux-framebuffer-bringup.yml"
    replace_once(
        path,
        """      - tests/vc4/linux-init.c
      - tests/vc4/stock-framebuffer-start.S
""",
        """      - tests/vc4/linux-init.c
      - tests/vc4/linux-runtime.S
      - tests/vc4/stock-framebuffer-start.S
""",
    )
    replace_once(
        path,
        """          if not handoff.get('signature_seen'):
              classification = 'stock-firmware-handoff'
""",
        """          if not handoff:
              classification = 'preflight-or-build'
          elif not handoff.get('signature_seen'):
              classification = 'stock-firmware-handoff'
""",
    )
    replace_once(
        path,
        """          next_steps = {
              'stock-firmware-handoff': (
""",
        """          next_steps = {
              'preflight-or-build': (
                  'Repair the source, payload, dual-target build, firmware '
                  'download, or focused regression failure before drawing '
                  'an architectural conclusion from an absent probe result.'
              ),
              'stock-firmware-handoff': (
""",
    )


def main() -> int:
    update_arm_payload()
    update_framebuffer()
    update_linux()
    update_workflow()
    print("updated VC4 stock probes for qtest-under-TCG clock handling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
