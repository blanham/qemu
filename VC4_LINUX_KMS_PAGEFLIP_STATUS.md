# VC4 inherited-master native KMS page flip

Validation passed: **false**

Frontier: **`vc4-kms-module-closure-regression`**

- DDC supplier root present: `True`
- Module closure loaded: `False`
- Native topology clear: `False`
- Existing render submission preserved: `False`
- Initial modeset completed: `False`
- Active CRTC inherited: `False`
- Page flip queued: `False`
- Flip-complete event received: `False`
- GETCRTC reports the new FB: `False`
- Page-flip witness completed: `False`
- Timeout: `False`
- Failure stage: `None`
- Failure errno: `None`

The modeset child leaves the first framebuffer on the shared drm_file. A second child creates a distinct framebuffer, queues DRM_IOCTL_MODE_PAGE_FLIP with the event flag, consumes DRM_EVENT_FLIP_COMPLETE, and checks that GETCRTC exposes the new framebuffer.
