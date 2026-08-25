# VC4 corrected-supplier native KMS modeset probe

Validation passed: **false**

Frontier: **`vc4-render-submit-regression`**

- Render submission preserved: `False`
- DDC supplier root present: `True`
- Native KMS topology clear: `False`
- SETCRTC modeset completed: `False`
- Failure stage: `None`
- Failure errno: `None`

The probe deliberately uses the already-open DRM card FD, so SETCRTC is issued by the file that owns DRM master. The fixture explicitly roots `i2c-bcm2835` before `vc4`; that supplier is not a module dependency of the VC4 consumer and must not be omitted from native-KMS tests.
