#!/usr/bin/env python3
"""Split x86 interrupt and exception logging while preserving ``-d int``.

The legacy CPU_LOG_INT bit remains untouched for unmigrated targets. The user
selector ``int`` becomes a composite alias that additionally enables x86's
provenance-aware ``irq`` and ``exception`` bits.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    new_count = text.count(new)
    if new_count == 1:
        return
    if new_count > 1:
        raise RuntimeError(f"{path}: transformed block appears {new_count} times")
    if new.endswith(old):
        owned_prefix = new[:-len(old)]
        if owned_prefix and owned_prefix in text:
            return
    old_count = text.count(old)
    if old_count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {old_count}")
    store(file_path, text.replace(old, new, 1))


def main() -> None:
    replace_once(
        "include/qemu/log.h",
        """#define LOG_INVALID_MEM    (1u << 23)

/* Lock/unlock output. */
""",
        """#define LOG_INVALID_MEM    (1u << 23)

/*
 * CPU_LOG_INT is the legacy mixed interrupt/exception category. New code
 * with reliable provenance should use one of the specific bits below; the
 * user-facing "int" selector remains their compatible aggregate.
 */
#define CPU_LOG_IRQ        (1u << 24)
#define CPU_LOG_EXCEPTION  (1u << 25)
#define CPU_LOG_INT_ALL \\
    (CPU_LOG_INT | CPU_LOG_IRQ | CPU_LOG_EXCEPTION)

/* Lock/unlock output. */
""",
    )
    replace_once(
        "util/log.c",
        """    { CPU_LOG_INT, "int",
      "show interrupts/exceptions in short format" },
""",
        """    { CPU_LOG_INT_ALL, "int",
      "show interrupts/exceptions (compatible aggregate)" },
    { CPU_LOG_IRQ, "irq",
      "x86 only: show asynchronous hardware interrupt delivery" },
    { CPU_LOG_EXCEPTION, "exception",
      "x86 only: show exceptions, traps, and software interrupts" },
""",
    )
    replace_once(
        "target/i386/tcg/system/seg_helper.c",
        """        qemu_log_mask(CPU_LOG_INT,
                      "Servicing hardware INT=0x%02x\\n", intno);
""",
        """        qemu_log_mask(CPU_LOG_IRQ,
                      "Servicing hardware INT=0x%02x\\n", intno);
""",
    )
    replace_once(
        "target/i386/tcg/system/seg_helper.c",
        """        qemu_log_mask(CPU_LOG_INT,
                      "Servicing virtual hardware INT=0x%02x\\n", intno);
""",
        """        qemu_log_mask(CPU_LOG_IRQ,
                      "Servicing virtual hardware INT=0x%02x\\n", intno);
""",
    )
    replace_once(
        "target/i386/tcg/excp_helper.c",
        """    qemu_log_mask(CPU_LOG_INT, "check_exception old: 0x%x new 0x%x\\n",
                env->old_exception, intno);
""",
        """    qemu_log_mask(CPU_LOG_EXCEPTION,
                  "check_exception old: 0x%x new 0x%x\\n",
                  env->old_exception, intno);
""",
    )
    replace_once(
        "target/i386/tcg/seg_helper.c",
        """    CPUX86State *env = &cpu->env;
    uint64_t last_pc = env->eip + env->segs[R_CS].base;

    if (qemu_loglevel_mask(CPU_LOG_INT)) {
""",
        """    CPUX86State *env = &cpu->env;
    uint64_t last_pc = env->eip + env->segs[R_CS].base;
    int log_mask = is_hw ? CPU_LOG_IRQ : CPU_LOG_EXCEPTION;

    if (qemu_loglevel_mask(log_mask)) {
""",
    )
    replace_once(
        "target/i386/tcg/system/smm_helper.c",
        """    qemu_log_mask(CPU_LOG_INT, "SMM: enter\\n");
    log_cpu_state_mask(CPU_LOG_INT, CPU(cpu), CPU_DUMP_CCOP);
""",
        """    qemu_log_mask(CPU_LOG_IRQ, "SMM: enter\\n");
    log_cpu_state_mask(CPU_LOG_IRQ, CPU(cpu), CPU_DUMP_CCOP);
""",
    )
    replace_once(
        "target/i386/tcg/system/smm_helper.c",
        """    qemu_log_mask(CPU_LOG_INT, "SMM: after RSM\\n");
    log_cpu_state_mask(CPU_LOG_INT, CPU(cpu), CPU_DUMP_CCOP);
""",
        """    qemu_log_mask(CPU_LOG_IRQ, "SMM: after RSM\\n");
    log_cpu_state_mask(CPU_LOG_IRQ, CPU(cpu), CPU_DUMP_CCOP);
""",
    )

    # Composite aliases are enabled only when every constituent bit is active.
    replace_once(
        "monitor/qmp-cmds.c",
        """        info->enabled = (mask & item->mask) != 0;
""",
        """        info->enabled = (mask & item->mask) == item->mask;
""",
    )
    replace_once(
        "scripts/wd40/apply-structured-log-control.py",
        """        info->enabled = (mask & item->mask) != 0;
""",
        """        info->enabled = (mask & item->mask) == item->mask;
""",
    )

    replace_once(
        "tests/unit/test-logging.c",
        """    g_assert_cmpint(int_mask, !=, 0);
    g_assert_cmpint(qemu_str_to_log_mask("+int,guest_errors"), ==,
""",
        """    g_assert_cmpint(int_mask, !=, 0);
    g_assert_cmpint(int_mask, ==, CPU_LOG_INT_ALL);
    g_assert_cmpint(qemu_str_to_log_mask("irq"), ==, CPU_LOG_IRQ);
    g_assert_cmpint(qemu_str_to_log_mask("exception"), ==,
                    CPU_LOG_EXCEPTION);
    g_assert_cmpint(qemu_str_to_log_mask("irq,exception"), ==,
                    CPU_LOG_IRQ | CPU_LOG_EXCEPTION);
    g_assert_cmpint(qemu_str_to_log_mask("int,-irq"), ==,
                    CPU_LOG_INT | CPU_LOG_EXCEPTION);
    g_assert_cmpint(qemu_str_to_log_mask("int,-exception"), ==,
                    CPU_LOG_INT | CPU_LOG_IRQ);
    g_assert_cmpint(qemu_str_to_log_mask("+int,guest_errors"), ==,
""",
    )
    replace_once(
        "qemu-options.hx",
        """        -d all,-tid,-int,-exec,-cpu
ERST
""",
        """        -d all,-tid,-int,-exec,-cpu

    On x86 TCG, ``int`` remains the compatible aggregate. Use ``irq`` for
    asynchronous IRQ, NMI, and SMI delivery, and ``exception`` for synchronous
    exceptions, traps, software interrupts, and machine checks. For example::

        -d int,-irq

    keeps x86 exception logs and legacy target logs while suppressing x86
    asynchronous interrupt delivery.
ERST
""",
    )
    replace_once(
        "scripts/wd40/apply-subtractive-log-mask.py",
        '''        -d all,-tid,-int,-exec,-cpu
ERST
""",
    )
''',
        '''        -d all,-tid,-int,-exec,-cpu

    On x86 TCG, ``int`` remains the compatible aggregate. Use ``irq`` for
    asynchronous IRQ, NMI, and SMI delivery, and ``exception`` for synchronous
    exceptions, traps, software interrupts, and machine checks. For example::

        -d int,-irq

    keeps x86 exception logs and legacy target logs while suppressing x86
    asynchronous interrupt delivery.
ERST
""",
    )
''',
    )
    replace_once(
        "docs/system/i386/wd40-qol.rst",
        """  qemu-system-x86_64 -machine pc-q35-7.0,amd-1tb-hole=on -cpu EPYC ...
""",
        """  qemu-system-x86_64 -machine pc-q35-7.0,amd-1tb-hole=on -cpu EPYC ...


Interrupt and exception logging
-------------------------------

The legacy ``-d int`` selector remains compatible. On the x86 TCG path it is
an aggregate of the legacy mixed category and two provenance-aware categories:

``irq``
  Asynchronous hardware and virtual IRQ delivery, NMI delivery, and SMM
  transitions caused by SMI.

``exception``
  Synchronous exceptions, traps, software interrupts, and machine-check
  delivery.

This makes periodic interrupt traffic removable without discarding fault
information. For example::

  qemu-system-x86_64 ... -d int,-irq

keeps x86 exception logs and still enables legacy ``int`` sites on targets
that have not yet been migrated. Selecting ``irq`` or ``exception`` alone
only enables the corresponding provenance-aware sites.
""",
    )
    replace_once(
        "docs/devel/wd40-monitor-v2.rst",
        """reuses ``qemu_set_log()`` for all state changes.
""",
        """reuses ``qemu_set_log()`` for all state changes.

Composite aliases are reported as enabled only when all constituent bits are
active. Consequently, selecting x86 ``irq`` alone does not misreport the
compatible aggregate ``int`` as enabled; selecting ``int`` enables and reports
``int``, ``irq``, and ``exception`` together.
""",
    )


if __name__ == "__main__":
    main()
