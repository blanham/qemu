WD40 C-only build contract
==========================

WD40 deliberately keeps QEMU's active build graph free of Rust.  The upstream
``rust/`` source directory may remain in the repository as provenance and to
reduce rebase churn, but configuring or building WD40 must not require
``rustc``, Cargo, rustdoc, rustfmt, or bindgen.

Policy
------

* C implementations are authoritative for devices that have parallel C and
  Rust implementations.  In particular, PL011 and HPET always select their C
  implementations.
* ``--disable-rust`` remains accepted for compatibility and is the default.
* ``--enable-rust`` fails with a clear WD40 policy error rather than silently
  changing the emulated device implementation according to host tooling.
* No Rust tool is discovered or invoked during configuration.
* Generated build metadata must contain no Rust compiler command, ``.rs`` input,
  Cargo command, or bindgen command.

Validation
----------

Run the source-only contract checks with::

  python3 scripts/ci/check-wd40-rustless.py

After configuring a build directory, validate both source policy and the active
build graph with::

  python3 scripts/ci/check-wd40-rustless.py build

The checker also verifies that generated Kconfig output selected ``HPET_C`` and
``PL011_C`` and did not expose ``CONFIG_HAVE_RUST`` or a Rust device backend.

Upstream rebases
----------------

``scripts/wd40/apply-rustless-build.py`` is an idempotent, marker-based
transformation used to reapply this policy after upstream build-system changes.
A failed marker match is intentional: it requires the rebase to inspect changed
upstream semantics instead of silently producing a partial Rustless build.
