# VC4 corrected-supplier native KMS modeset probe

Validation passed: **false**

Frontier: **`vc4-kms-modeset-resource-read`**

- Render submission preserved: `True`
- DDC supplier root present: `True`
- Native KMS topology clear: `True`
- SETCRTC modeset completed: `False`
- Failure stage: `resource-read`
- Failure errno: `14`

The probe deliberately uses the already-open DRM card FD, so SETCRTC is issued by the file that owns DRM master. The fixture explicitly roots `i2c-bcm2835` before `vc4`; that supplier is not a module dependency of the VC4 consumer and must not be omitted from native-KMS tests.
