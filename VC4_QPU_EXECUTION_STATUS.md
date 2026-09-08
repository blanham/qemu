# VC4 QPU execution checkpoint

Recorded after validation on 2026-09-08 UTC (2026-09-07 America/Chicago).
This is an execution-core checkpoint, **not a completed GLES2 triangle**.

## Exact source and evidence

| Item | Value |
| --- | --- |
| Branch | `agent/vc4-linux-mesa-gles2-frontier` |
| Validated product commit | `633f98a545d141049dbd4f95386d739ca36f9383` |
| Validated product tree | `b2f382ad6a900c860368b2d13df6ae7b7d63fdc7` |
| Workflow checkout commit | `d32afb18572a7a336f3e106d748f4647fdd610b3` |
| Focused validation run | `34181541610`, attempt `1` |
| Focused validation job | `101921476453` |
| Evidence artifact | `10039204158`, `vc4-qpu-contracts-34181541610-1` |
| Evidence archive SHA-256 | `f503e5674d15ae91b5bc0bb1a7664e3fa0ba342f0febb03c451d42644515371c` |
| Executor Git blob | `2094c27f6b0f80f0bf1727fb1a1dd903c7b6e2ad` |
| Independent-test Git blob | `abff033fe9f40ec802b91f4a3208156882849875` |
| Host compiler | GCC 13.3.0, x86_64, Ubuntu 24.04 runner |

[Validation run](https://github.com/blanham/qemu/actions/runs/34181541610)
/ [Evidence artifact](https://github.com/blanham/qemu/actions/runs/34181541610/artifacts/10039204158)
/ [Product commit](https://github.com/blanham/qemu/commit/633f98a545d141049dbd4f95386d739ca36f9383).

The downloaded 47,655-byte ZIP was inspected, its digest matched the Actions
artifact digest, and all four TAP files were read. The archive contains
`BASE_SHA`, `PRODUCT_SHA`, `TESTED_TREE`, source checksums, the generated
product patch, configure/build logs, TAP output, and both V3D regression logs.
The materialized C and Meson changes were published only after validation;
ordinary builds of the product commit do not require running the materializer.

## Executed validation

| Check | Observed result |
| --- | --- |
| Applying the QPU integration/repair script twice | Identical output checksums |
| Focused unit-test compilation | Passed |
| Existing measured-shader suite | 4/4 passed |
| New independent execution contracts | 13/13 passed |
| Both suites under ASan, UBSan, and float-cast-overflow instrumentation | 17/17 passed |
| Full `qemu-system-aarch64` build | Passed |
| V3D register, clear-render, and control-flow smoke test | Passed |
| Shader-record and QPU frontier smoke test | Passed |
| Publication with tested-tree equality check | Passed |

The normal and sanitizer builds are separate. Sanitizer validation applies to
the two QPU unit-test executables, not to a fully sanitized Linux guest run.

### TAP test identities

The following four tests passed in both builds:

```text
/vc4/qpu/measured-vs
/vc4/qpu/measured-cs
/vc4/qpu/measured-fs
/vc4/qpu/fail-closed
```

The following thirteen tests passed in both builds:

```text
/vc4/qpu/contract/alu-write-swap
/vc4/qpu/contract/load-write-swap
/vc4/qpu/contract/integer-pack
/vc4/qpu/contract/pack-accumulator
/vc4/qpu/contract/packed-tlb-rejected
/vc4/qpu/contract/float-pack-rejected
/vc4/qpu/contract/ftoi-boundaries
/vc4/qpu/contract/ftoi-invalid
/vc4/qpu/contract/shared-uniform
/vc4/qpu/contract/code-read-span
/vc4/qpu/contract/uniform-read-span
/vc4/qpu/contract/end-delay
/vc4/qpu/contract/program-limit
```

## Repairs made concrete in the product source

* Added the missing math declaration for `isfinite`, resolving the compiler/
  linker failure. Implicit function declarations are now fatal in this gate.
* Corrected ADD/MUL destination-bank selection for both ALU and load-immediate
  instructions: `ws=0` writes ADD to A and MUL to B; `ws=1` swaps them.
* Fixed the float-to-int upper bound. Comparing a binary32 value with
  `INT32_MAX` rounds the latter to positive 2^31, admitting an undefined C
  cast. The supported conversion interval is now `[-2^31, 2^31)`; nonfinite
  and out-of-range inputs fail before destination writeback. A separate local
  UBSan reproducer confirmed the old positive-2^31 failure and corrected guard.
* Routed PM=0 packing to register file A after write-swap, not unconditionally
  to the ADD pipeline. Integer halfword writes preserve the other halfword;
  accumulator writes bypass register-file-A packing. Floating-point halfword
  packing is explicitly rejected until real float16 conversion is implemented.
  Packed peripheral writes remain outside the supported subset.
* Validate the entire 4-byte uniform or 8-byte instruction read span before
  invoking the memory callback, rather than checking only the starting address.

The independent tests exercise all sixteen lanes, both write-swap settings,
untouched destination banks, invalid conversion in a nonzero lane, one shared
uniform consumption for both read ports, boundary-spanning reads rejected
before the callback, missing termination delay slots, and the 64-instruction
execution budget.

The workflow also keeps evidence outside QEMU's configure-owned `build/`
directory, runs focused tests before the full emulator build, and refuses to
publish a different source tree after rebasing onto a concurrently changed
branch. Two workflow-only failures during this tranche were retained in runs
`34181210678` (precreated build directory) and `34181469465` (job-level runner
context). Neither reached semantic execution; run `34181541610` cleared the
corrected gate. Test expectations were not weakened to obtain the pass.

## Remaining implementation boundary

`vc4_qpu_exec.c` is a bounded, separately tested interpreter for the current
subset. The measured Mesa VS, CS, and FS binaries execute in the unit harness.
This does **not** establish a complete QPU ISA implementation or hardware-wide
floating-point conformance. Branches, general predicates/flags, TMUs, broader
pack/unpack modes, and other unimplemented forms still fail explicitly. Native
host floating-point arithmetic remains in the current subset; cross-host and
physical-hardware numerical equivalence are not claimed by these tests.
Rejecting exceptional FTOI operands is a supported-subset policy, not a claim
that the physical QPU faults in precisely the same way.

The executor is compiled into the emulator but is not yet connected to V3D
primitive execution. `bcm2835_v3d.c` still rejects GL array primitives (`0x21`)
with the established unsupported/error path. There is no new triangle-specific
shortcut, hardcoded framebuffer fill, or fabricated draw completion.

The publication dispatched Linux/Mesa run
[`34181955462`](https://github.com/blanham/qemu/actions/runs/34181955462) against
product commit `633f98a545d141049dbd4f95386d739ca36f9383`. This checkpoint does
not assert that run's outcome. Check its exact source and
`VC4_LINUX_MESA_GLES2_STATUS.json`; a green frontier-harness workflow must not be
confused with `passed: true` for actual triangle/readback validation.

## Next implementation contract

Connect validated shader records and per-stage attribute selection/stride/VPM
offsets to bounded vertex batches, then execute the coordinate and vertex
programs from guest memory and retain their typed outputs for binning. Share a
real shader-record decoder with diagnostics rather than making log text or
fixed diagnostic trace windows into an execution interface. Diagnostic
PC/record deduplication must never suppress legitimate repeated jobs.

The caller-owned QPU context is mutated during execution; failure does not
promise whole-program rollback. Future dispatch must discard failed work and
must not publish partial outputs as successful binning or rendering. Preserve
precise faults, bounds, real synchronization/completion, and reset/migration
ownership before accepting primitive packets. The subsequent binning,
rasterization, fragment dispatch, and tile-store stages must produce the actual
Mesa readback pixels before the rendering checkpoint can become clear.

## Reproduction from the product commit

From a clean checkout, with the usual QEMU build dependencies installed:

```sh
./configure --target-list=aarch64-softmmu --disable-docs \
  --extra-cflags=-Werror=implicit-function-declaration
ninja -C build tests/unit/test-vc4-qpu tests/unit/test-vc4-qpu-contract
build/tests/unit/test-vc4-qpu --tap -k
build/tests/unit/test-vc4-qpu-contract --tap -k
ninja -C build qemu-system-aarch64
python3 scripts/vc4/v3d-smoke.py --qemu build/qemu-system-aarch64
python3 scripts/vc4/v3d-shader-frontier-smoke.py \
  --qemu build/qemu-system-aarch64
```

The sanitizer build commands and options are retained in
`.github/workflows/vc4-v3d-shader-frontier.yml` at the recorded source revision.

Architecture references: Broadcom *VideoCore IV 3D Architecture Reference
Guide*, VideoCoreIV-AG100-R, pp. 18-20, 27, 30-31, and 35-37. These document the
register-bank mapping, packing, and instruction contracts; the measured Mesa
fixture provenance remains in `VC4_LINUX_MESA_GLES2_STATUS.{json,md}` and
`tests/unit/test-vc4-qpu.c`.
