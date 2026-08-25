# VC4 inherited-master native KMS modeset

Validation passed: **false**

Frontier: **`vc4-kms-modeset-msync-dumb`**

- DDC supplier root present: `True`
- Module closure loaded: `True`
- Native topology clear: `True`
- Existing render submission preserved: `True`
- SETCRTC entered: `False`
- SETCRTC completed: `False`
- Supervised modeset completed: `False`
- Timeout: `False`
- Failure stage: `msync-dumb`
- Failure errno: `22`

The child inherits the already-open primary-node FD, preserving DRM-master ownership. GETRESOURCES receives all four user arrays (FB, CRTC, connector, and encoder), eliminating the EFAULT in the first one-shot witness.
