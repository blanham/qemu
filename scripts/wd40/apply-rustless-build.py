#!/usr/bin/env python3
"""Apply WD40's C-only build contract to the QEMU source tree.

The transformation is deliberately marker-based and idempotent so it can be
rerun after routine upstream rebases. Rust sources remain in the repository as
upstream provenance, but the active build graph must not discover or compile
Rust code.
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
    count = text.count(old)
    if count == 1:
        store(file_path, text.replace(old, new, 1))
        return
    if count == 0 and new in text:
        return
    raise RuntimeError(f"{path}: expected one replacement site, found {count}")


def normalize_final_newline(path: str) -> None:
    file_path, text = load(path)
    store(file_path, text.rstrip("\n") + "\n")


def transform_meson() -> None:
    file_path, text = load("meson.build")

    project_end = "        version: files('VERSION'))"
    if project_end not in text:
        raise RuntimeError("meson.build: project declaration end marker missing")
    project_end_at = text.index(project_end) + len(project_end)
    project = """project('qemu', ['c'], meson_version: '>=1.5.0',
        default_options: ['warning_level=1', 'c_std=gnu11',
                          'cpp_std=gnu++23', 'b_colorout=auto',
                          'b_staticpic=false', 'stdsplit=false',
                          'optimization=2', 'b_pie=true'],
        version: files('VERSION'))"""
    text = project + text[project_end_at:]

    setup_start = "add_test_setup('quick'"
    setup_end = "meson.add_postconf_script"
    setup_start_at = text.index(setup_start)
    setup_end_at = text.index(setup_end, setup_start_at)
    setups = """add_test_setup('quick', exclude_suites: ['slow', 'thorough'], is_default: true)
add_test_setup('slow', exclude_suites: ['thorough'],
               env: ['G_TEST_SLOW=1', 'SPEED=slow'])
add_test_setup('thorough',
               env: ['G_TEST_SLOW=1', 'SPEED=thorough'])

"""
    text = text[:setup_start_at] + setups + text[setup_end_at:]

    text = text.replace("rust = import('rust')\n", "")

    rust_start = "have_rust = add_languages('rust'"
    rust_end = "dtrace = not_found"
    rust_policy = """if get_option('rust').enabled()
  error('Rust support is intentionally disabled in the WD40 fork')
endif
have_rust = false

"""
    if rust_start in text:
        rust_start_at = text.index(rust_start)
        rust_end_at = text.index(rust_end, rust_start_at)
        text = text[:rust_start_at] + rust_policy + text[rust_end_at:]
    elif rust_policy not in text:
        raise RuntimeError("meson.build: Rust toolchain discovery marker missing")

    subdir_line = "  subdir('rust')\n"
    if text.count(subdir_line) > 1:
        raise RuntimeError("meson.build: multiple Rust subtree inclusions found")
    text = text.replace(subdir_line, "")

    original_root_crate = (
        "rust_root_crate = find_program('scripts/rust/rust_root_crate.sh')"
    )
    if original_root_crate in text:
        text = text.replace(original_root_crate, "rust_root_crate = not_found", 1)
    elif "rust_root_crate = not_found" not in text:
        raise RuntimeError("meson.build: Rust root-crate helper marker missing")

    text = text.replace(
        "config_host_data.set('CONFIG_HAVE_RUST', have_rust)\n",
        "",
    )
    text = text.replace(
        "  (hv_balloon ? ['CONFIG_HV_BALLOON_POSSIBLE=y'] : []) + \\\n"
        "  (have_rust ? ['CONFIG_HAVE_RUST=y'] : [])",
        "  (hv_balloon ? ['CONFIG_HV_BALLOON_POSSIBLE=y'] : [])",
    )

    rust_summary = """summary_info += {'Rust support':      have_rust}
if have_rust
  summary_info += {'Rust target':     rust.compiler_target(native: false)}
  summary_info += {'rustc':           ' '.join(rustc.cmd_array())}
  summary_info += {'rustc version':   rustc.version()}
  summary_info += {'rustdoc':         rustdoc}
  summary_info += {'bindgen':         bindgen.full_path()}
  summary_info += {'bindgen version': bindgen.version()}
endif
"""
    rust_summary_disabled = "summary_info += {'Rust support':      false}\n"
    if rust_summary in text:
        text = text.replace(rust_summary, rust_summary_disabled, 1)
    elif rust_summary_disabled not in text:
        raise RuntimeError("meson.build: Rust compilation summary marker missing")

    store(file_path, text)


def main() -> None:
    transform_meson()

    replace_once(
        "configure",
        """  --enable-rust) rust=enabled
  ;;
  --disable-rust) rust=disabled""",
        """  --enable-rust)
      error_exit "Rust support is intentionally disabled in the WD40 fork"
  ;;
  --disable-rust) rust=disabled""",
    )

    replace_once(
        "meson_options.txt",
        """option('rust', type: 'feature', value: 'disabled',
       description: 'Rust support')
option('strict_rust_lints', type: 'boolean', value: false,
       description: 'Enable stricter set of Rust warnings')""",
        """option('rust', type: 'feature', value: 'disabled',
       description: 'unsupported compatibility option; WD40 is C-only')
option('strict_rust_lints', type: 'boolean', value: false,
       description: 'ignored compatibility option; Rust is disabled')""",
    )

    replace_once(
        "Kconfig.host",
        """config HAVE_RUST
    bool

""",
        "",
    )
    replace_once(
        "hw/char/Kconfig",
        """config PL011
    bool
    # The PL011 has both a Rust and a C implementation
    select PL011_C if !HAVE_RUST
    select X_PL011_RUST if HAVE_RUST
""",
        """config PL011
    bool
    select PL011_C
""",
    )
    replace_once(
        "hw/timer/Kconfig",
        """config HPET
    bool
    default y if PC
    # The HPET has both a Rust and a C implementation
    select HPET_C if !HAVE_RUST
    select X_HPET_RUST if HAVE_RUST
""",
        """config HPET
    bool
    default y if PC
    select HPET_C
""",
    )
    replace_once(
        "include/qemu/log.h",
        "ssize_t rust_fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream);\n\n",
        "",
    )
    replace_once(
        "util/log.c",
        """
#ifdef CONFIG_HAVE_RUST
ssize_t rust_fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream)
{
    /*
     * Same as fwrite, but return -errno because Rust libc does not provide
     * portable access to errno. :(
     */
    int ret = fwrite(ptr, size, nmemb, stream);
    return ret < 0 ? -errno : 0;
}
#endif""",
        "",
    )
    normalize_final_newline("util/log.c")


if __name__ == "__main__":
    main()
