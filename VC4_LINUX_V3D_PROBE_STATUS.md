# VC4 Linux V3D driver probe

Validation passed: **false**

- Source commit: `6640873a22c2adc6704701a7fcb8e99cf3d1f1f2`
- Workflow run: `32423778810`
- Best device-tree variant: `kms`
- Classification: `linux-vc4-component-bind-blocked`

| Variant | Result | DT V3D | card0 | renderD128 | IDENT | GEM BO |
|---|---:|---:|---:|---:|---:|---:|
| base | False | False | False | False | False | False |
| kms | False | True | False | False | False | False |
| fkms | False | True | False | False | False | False |
