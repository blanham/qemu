# VC4 inherited-master native KMS modeset

Validation passed: **true**

Frontier: **`linux-vc4-kms-modeset-clear`**

- DDC supplier root present: `True`
- Module closure loaded: `True`
- Native topology clear: `True`
- Existing render submission preserved: `True`
- SETCRTC entered: `True`
- SETCRTC completed: `True`
- Supervised modeset completed: `True`
- Timeout: `False`
- Failure stage: `None`
- Failure errno: `None`

The child inherits the already-open primary-node FD, preserving DRM-master ownership. GETRESOURCES receives all four user arrays (FB, CRTC, connector, and encoder), eliminating the EFAULT in the first one-shot witness.
