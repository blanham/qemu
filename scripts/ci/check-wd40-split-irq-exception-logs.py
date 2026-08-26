#!/usr/bin/env python3
"""Validate WD40's x86 IRQ/exception logging split."""

import base64
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BOOT_CODE = base64.b64decode(
    "+jHAjtiOwI7QvAB8xwYYAH98oxoAxwaAAI58o4IAsBHmIOh5AOag6HQAsCDmIehtALAo5qHoZgCwBOYh6F8AsALmoehYALAB5iHoUQDmoehMALD+5iGw/+ahsDbmQ7icLuZAiODmQA8L+/SAPqV8AHT4sETm6br0ALAQ7vTr/VNQsEXm6ViJ44NHAgJbz1DGBqV8AbBJ5umwIOYgWM9QMcDmgFjDAA=="
)
if len(BOOT_CODE) > 510:
    raise SystemExit(f"x86 log-split smoke guest is too large: {len(BOOT_CODE)} bytes")
IMAGE = BOOT_CODE.ljust(510, b"\0") + b"\x55\xaa"


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def need(path, *markers):
    data = source(path)
    missing = [marker for marker in markers if marker not in data]
    if missing:
        raise SystemExit(f"{path}: missing {missing!r}")


def validate_static():
    if len(IMAGE) != 512 or IMAGE[510:] != b"\x55\xaa":
        raise SystemExit("x86 log-split smoke image is not a valid 512-byte boot sector")
    need("include/qemu/log.h", "CPU_LOG_IRQ        (1u << 24)",
         "CPU_LOG_EXCEPTION  (1u << 25)", "CPU_LOG_INT_ALL",
         "CPU_LOG_INT | CPU_LOG_IRQ | CPU_LOG_EXCEPTION")
    need("util/log.c", '{ CPU_LOG_INT_ALL, "int",',
         '{ CPU_LOG_IRQ, "irq",', '{ CPU_LOG_EXCEPTION, "exception",')
    need("target/i386/tcg/system/seg_helper.c", "qemu_log_mask(CPU_LOG_IRQ,",
         "Servicing hardware INT=0x%02x", "Servicing virtual hardware INT=0x%02x")
    need("target/i386/tcg/seg_helper.c",
         "int log_mask = is_hw ? CPU_LOG_IRQ : CPU_LOG_EXCEPTION;",
         "qemu_loglevel_mask(log_mask)")
    need("target/i386/tcg/excp_helper.c", "qemu_log_mask(CPU_LOG_EXCEPTION,",
         "check_exception old: 0x%x new 0x%x")
    need("target/i386/tcg/system/smm_helper.c", 'CPU_LOG_IRQ, "SMM: enter',
         'CPU_LOG_IRQ, "SMM: after RSM')
    legacy = []
    for path in (ROOT / "target/i386").rglob("*.[ch]"):
        if re.search(r"\bCPU_LOG_INT\b", path.read_text(encoding="utf-8")):
            legacy.append(str(path.relative_to(ROOT)))
    if legacy:
        raise SystemExit("unmigrated x86 CPU_LOG_INT sites: " + ", ".join(sorted(legacy)))
    need("monitor/qmp-cmds.c", "info->enabled = (mask & item->mask) == item->mask;")
    if "info->enabled = (mask & item->mask) != 0;" in source("monitor/qmp-cmds.c"):
        raise SystemExit("monitor/qmp-cmds.c: stale partial composite test")
    need("tests/unit/test-logging.c", "int_mask, ==, CPU_LOG_INT_ALL",
         'qemu_str_to_log_mask("irq")', 'qemu_str_to_log_mask("exception")',
         'qemu_str_to_log_mask("int,-irq")')
    need("qemu-options.hx", "On x86 TCG, ``int`` remains the compatible aggregate.",
         "-d int,-irq")
    need("docs/system/i386/wd40-qol.rst", "Interrupt and exception logging",
         "qemu-system-x86_64 ... -d int,-irq")


def qmp_aliases(binary):
    commands = [
        ("irq", "replace", ["irq"]),
        ("irq-exception", "enable", ["exception"]),
        ("int", "replace", ["int"]),
        ("int-minus-irq", "disable", ["irq"]),
    ]
    messages = [{"execute": "qmp_capabilities", "id": "capabilities"}]
    for ident, action, categories in commands:
        messages.append({"execute": "set-log-categories",
                         "arguments": {"action": action, "categories": categories},
                         "id": ident})
    messages.append({"execute": "quit", "id": "quit"})
    run = subprocess.run(
        [str(binary), "-machine", "none", "-display", "none", "-nodefaults",
         "-S", "-qmp", "stdio"],
        input="\n".join(json.dumps(item) for item in messages) + "\n",
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=45, check=False)
    replies = {}
    for line in run.stdout.splitlines():
        if line.lstrip().startswith("{"):
            item = json.loads(line)
            if "id" in item:
                replies[str(item["id"])] = item
    expected = {
        "irq": {"irq"},
        "irq-exception": {"irq", "exception"},
        "int": {"int", "irq", "exception"},
        "int-minus-irq": {"exception"},
    }
    for ident, wanted in expected.items():
        reply = replies.get(ident)
        if not reply or "error" in reply or not isinstance(reply.get("return"), list):
            raise SystemExit(f"{ident}: bad QMP reply {reply!r}; stderr={run.stderr}")
        enabled = {entry["name"] for entry in reply["return"] if entry.get("enabled")}
        if enabled != wanted:
            raise SystemExit(f"{ident}: enabled {sorted(enabled)}, expected {sorted(wanted)}")


def read_diagnostic(path, *, encoding="utf-8"):
    if not path.is_file():
        return "<missing>"
    return path.read_text(encoding=encoding, errors="replace")


def smoke(binary, image, directory, name, mask):
    log = directory / f"{name}.log"
    witness = directory / f"{name}.debug"
    command = [
        str(binary), "-machine", "pc,accel=tcg", "-cpu", "qemu64", "-m", "16M",
        "-drive", f"if=floppy,format=raw,file={image}", "-boot", "a",
        "-display", "none", "-serial", "none", "-monitor", "none", "-no-reboot",
        "-chardev", f"file,id=debug,path={witness}",
        "-device", "isa-debugcon,iobase=0xe9,chardev=debug",
        "-device", "isa-debug-exit,iobase=0xf4,iosize=0x04",
        "-d", mask, "-D", str(log),
    ]
    try:
        run = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=45, check=False)
    except subprocess.TimeoutExpired as exc:
        debug = read_diagnostic(witness, encoding="ascii")
        log_tail = read_diagnostic(log)[-4000:]
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
        raise SystemExit(
            f"{name}: guest timed out; witness={debug!r}; "
            f"stderr={stderr!r}; log-tail={log_tail!r}"
        ) from exc
    debug = read_diagnostic(witness, encoding="ascii")
    if run.returncode != 33 or debug != "EID":
        raise SystemExit(
            f"{name}: guest witness failed, rc={run.returncode}, "
            f"witness={debug!r}, stderr={run.stderr.decode(errors='replace')!r}"
        )
    return log.read_text(encoding="utf-8", errors="replace")


def validate_runtime(build):
    binary = build / "qemu-system-x86_64"
    if not binary.is_file():
        raise SystemExit(f"missing {binary}")
    qmp_aliases(binary)
    with tempfile.TemporaryDirectory(prefix="wd40-log-split-") as tmp:
        directory = Path(tmp)
        image = directory / "smoke.img"
        image.write_bytes(IMAGE)
        exception = smoke(binary, image, directory, "exception", "exception")
        irq = smoke(binary, image, directory, "irq", "irq")
        filtered = smoke(binary, image, directory, "filtered", "int,-irq")
        exc = re.compile(r"check_exception .*new 0x6")
        if not exc.search(exception) or "Servicing hardware INT=" in exception:
            raise SystemExit("exception category did not isolate #UD")
        if "Servicing hardware INT=0x20" not in irq or exc.search(irq):
            raise SystemExit("irq category did not isolate PIT IRQ0")
        if not exc.search(filtered) or "Servicing hardware INT=" in filtered:
            raise SystemExit("int,-irq did not retain exceptions and remove IRQ noise")
    print("x86 IRQ/exception split: parser, QMP aliases, and guest routing validated")


validate_static()
if len(sys.argv) > 2:
    raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
if len(sys.argv) == 2:
    validate_runtime(Path(sys.argv[1]).resolve())
