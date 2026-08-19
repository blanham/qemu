#!/usr/bin/env python3
"""Exercise VC4 scalar processor-control registers and P-reg mutexes."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import struct
import subprocess
import tempfile


# mov r2, 1
# mov r0, p16       -> 0, and claim p16
# mov r1, p16       -> 1, p16 remains claimed
# mov p16, r2       -> release; the source value must be ignored
# mov r3, p16       -> 0, and claim p16 again
# mov p16, r2       -> release
# mov r4, p17       -> 0, and claim independent p17
# mov r5, p17       -> 1
# mov p17, r2       -> release
# mov p0, r2        -> ordinary processor-control register write
# mov r6, p0        -> ordinary processor-control register read, 1
# mov r7, p15       -> no outstanding synchronous reads, 0
# halt
FIRMWARE_HALFWORDS = (
    0x6012,
    0xCC20, 0x0010,
    0xCC21, 0x0010,
    0xCC10, 0x0002,
    0xCC23, 0x0010,
    0xCC10, 0x0002,
    0xCC24, 0x0011,
    0xCC25, 0x0011,
    0xCC11, 0x0002,
    0xCC00, 0x0002,
    0xCC26, 0x0000,
    0xCC27, 0x000F,
    0x0000,
)

HALT_PC = (len(FIRMWARE_HALFWORDS) - 1) * 2
EXPECTED_REGS = {
    0: 0,
    1: 1,
    2: 1,
    3: 0,
    4: 0,
    5: 1,
    6: 1,
    7: 0,
}


def build_firmware(path: Path) -> None:
    path.write_bytes(
        struct.pack(
            f"<{len(FIRMWARE_HALFWORDS)}H",
            *FIRMWARE_HALFWORDS,
        )
    )


def find_cpu_state(log: str) -> str:
    match = re.search(
        rf"pc={HALT_PC:08x}\s+sr=[0-9a-f]{{8}}(.*?)(?=\npc=|\Z)",
        log,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise SystemExit(
            f"no CPU-state block for HALT at PC 0x{HALT_PC:x}\n"
            f"last log bytes:\n{log[-4000:]}"
        )
    return match.group(0)


def check_state(state: str) -> None:
    failures = []
    for reg, value in EXPECTED_REGS.items():
        if not re.search(
            rf"r{reg}\s*=\s*{value:08x}", state, re.IGNORECASE
        ):
            failures.append(f"r{reg} != 0x{value:08x}")

    if failures:
        raise SystemExit(
            "processor-control register smoke test failed:\n"
            + "\n".join(f"  {failure}" for failure in failures)
            + f"\nfinal state:\n{state}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-vc4"),
        help="path to qemu-system-vc4",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="seconds to let the halted machine remain alive",
    )
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-preg-") as temp_dir:
        temp = Path(temp_dir)
        firmware = temp / "preg-mutex.bin"
        log_path = temp / "qemu.log"
        build_firmware(firmware)

        command = (
            str(qemu),
            "-M", "vc4-vpu",
            "-m", "1M",
            "-kernel", str(firmware),
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-singlestep",
            "-d", "cpu",
            "-D", str(log_path),
        )

        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            # A halted system has no host shutdown device and normally waits.
            pass
        else:
            if completed.returncode != 0:
                raise SystemExit(
                    f"QEMU exited with status {completed.returncode}:\n"
                    f"{completed.stderr}"
                )

        if not log_path.is_file():
            raise SystemExit("QEMU did not create the requested CPU log")

        log = log_path.read_text(encoding="utf-8", errors="replace")
        check_state(find_cpu_state(log))

    print("VC4 processor-control register smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
