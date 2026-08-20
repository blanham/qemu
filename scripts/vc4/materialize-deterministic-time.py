#!/usr/bin/env python3
"""Materialize deterministic virtual-time support in all VC4 stock probes.

The Raspberry Pi firmware contains ordinary timer-based delays.  Under a slow
single-threaded heterogeneous TCG build, tying those delays to host real time
makes the observed frontier depend on host load.  This helper makes the timing
mode explicit and reproducible:

* retain qtest for physical-memory inspection;
* use clock_step only when the selected accelerator actually supports it;
* otherwise run TCG with an optional fixed -icount shift;
* record the selected timing mode in every result document.

The transformation is intentionally idempotent so CI can apply it before the
full build and probes, then publish the resulting source only after validation.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / "scripts/vc4/raspi3-stock-arm-payload.py"
FRAMEBUFFER = ROOT / "scripts/vc4/raspi3-stock-framebuffer.py"
LINUX = ROOT / "scripts/vc4/raspi3-stock-linux.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path.relative_to(ROOT)}: {label}: expected one anchor, "
            f"found {count}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def add_icount_cli(path: Path) -> None:
    replace_once(
        path,
        """    args = parser.parse_args()

    if args.seconds <= 0:
""",
        """    parser.add_argument(
        "--icount-shift",
        type=int,
        default=None,
        help=(
            "optional fixed QEMU icount shift; virtual nanoseconds per "
            "guest instruction are 2**shift"
        ),
    )
    args = parser.parse_args()

    if args.seconds <= 0:
""",
        "icount CLI",
    )
    replace_once(
        path,
        """    if args.clock_step_ns <= 0:
        parser.error("--clock-step-ns must be positive")
""",
        """    if args.clock_step_ns <= 0:
        parser.error("--clock-step-ns must be positive")
    if args.icount_shift is not None and not 0 <= args.icount_shift <= 20:
        parser.error("--icount-shift must be between 0 and 20")
""",
        "icount validation",
    )


def add_icount_command(path: Path) -> None:
    replace_once(
        path,
        """            "-accel", "tcg,thread=single",
            "-display", "none",
""",
        """            "-accel", "tcg,thread=single",
        ]
        if args.icount_shift is not None:
            command.extend([
                "-icount",
                f"shift={args.icount_shift},align=off,sleep=off",
            ])
        command.extend([
            "-display", "none",
""",
        "icount command opening",
    )
    replace_once(
        path,
        """            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]
""",
        """            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ])
""",
        "icount command closing",
    )


def add_result_icount(path: Path) -> None:
    replace_once(
        path,
        """            "qtest_clock_step_ns": args.clock_step_ns,
""",
        """            "icount_shift": args.icount_shift,
            "qtest_clock_step_ns": args.clock_step_ns,
""",
        "result icount field",
    )


def update_arm() -> None:
    add_icount_cli(ARM)
    add_icount_command(ARM)
    add_result_icount(ARM)


def replace_qtest_clock_class(path: Path, *, has_readq: bool) -> None:
    readq = """
    def readq(self, address: int) -> int:
        fields = self.command(f"readq 0x{address:x}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)
""" if has_readq else ""
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
    replace_once(path, old, new, "qtest clock capability")


def replace_clock_loop(path: Path, marker_read: str) -> None:
    old = f"""            qtest_clock_ns = 0
            while time.monotonic() < deadline:
                qtest_clock_ns = qtest.advance(args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                {marker_read}
"""
    new = f"""            qtest_clock_ns: int | None = None
            clock_step_available = True
            while time.monotonic() < deadline:
                if clock_step_available:
                    stepped = qtest.try_advance(args.clock_step_ns)
                    if stepped is None:
                        clock_step_available = False
                        result["qtest_clock_step_supported"] = False
                        result["clock_mode"] = (
                            "tcg-icount" if args.icount_shift is not None
                            else "tcg-realtime"
                        )
                    else:
                        qtest_clock_ns = stepped
                        result["qtest_clock_steps"] += 1
                        result["qtest_clock_step_supported"] = True
                        result["clock_mode"] = "qtest-controlled"
                {marker_read}
"""
    replace_once(path, old, new, "clock fallback loop")


def update_framebuffer() -> None:
    replace_qtest_clock_class(FRAMEBUFFER, has_readq=True)
    add_icount_cli(FRAMEBUFFER)
    add_icount_command(FRAMEBUFFER)
    add_result_icount(FRAMEBUFFER)
    replace_once(
        FRAMEBUFFER,
        """            "qtest_clock_steps": 0,
""",
        """            "qtest_clock_steps": 0,
            "qtest_clock_step_supported": None,
            "clock_mode": "probing",
""",
        "framebuffer clock result fields",
    )
    replace_clock_loop(
        FRAMEBUFFER,
        "magic = qtest.readq(RESULT_ADDRESS)",
    )


def update_linux() -> None:
    replace_qtest_clock_class(LINUX, has_readq=False)
    add_icount_cli(LINUX)
    add_icount_command(LINUX)
    add_result_icount(LINUX)
    replace_once(
        LINUX,
        """            "qtest_clock_steps": 0,
            "linux_banner_seen": False,
""",
        """            "qtest_clock_steps": 0,
            "qtest_clock_step_supported": None,
            "clock_mode": "probing",
            "linux_banner_seen": False,
""",
        "Linux clock result fields",
    )
    old = """            qtest_clock_ns = 0
            text = ""
            while time.monotonic() < deadline:
                qtest_clock_ns = qtest.advance(args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                text = serial_text(serial_path)
"""
    new = """            qtest_clock_ns: int | None = None
            clock_step_available = True
            text = ""
            while time.monotonic() < deadline:
                if clock_step_available:
                    stepped = qtest.try_advance(args.clock_step_ns)
                    if stepped is None:
                        clock_step_available = False
                        result["qtest_clock_step_supported"] = False
                        result["clock_mode"] = (
                            "tcg-icount" if args.icount_shift is not None
                            else "tcg-realtime"
                        )
                    else:
                        qtest_clock_ns = stepped
                        result["qtest_clock_steps"] += 1
                        result["qtest_clock_step_supported"] = True
                        result["clock_mode"] = "qtest-controlled"
                text = serial_text(serial_path)
"""
    replace_once(LINUX, old, new, "Linux clock fallback loop")


def main() -> int:
    update_arm()
    update_framebuffer()
    update_linux()
    print("materialized deterministic timing in all VC4 stock probes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
