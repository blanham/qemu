# VC4 inherited-master native KMS page flip

Validation passed: **true**

Frontier: **`linux-vc4-kms-pageflip-clear`**

- DDC supplier root present: `True`
- Module closure loaded: `True`
- Native topology clear: `True`
- Existing render submission preserved: `True`
- Initial modeset completed: `True`
- Active CRTC inherited: `True`
- Page flip queued: `True`
- Flip-complete event received: `True`
- GETCRTC reports the new FB: `True`
- Page-flip witness completed: `True`
- Timeout: `False`
- Failure stage: `None`
- Failure errno: `None`

The modeset child leaves the first framebuffer on the shared drm_file. A second child creates a distinct framebuffer, queues DRM_IOCTL_MODE_PAGE_FLIP with the event flag, consumes DRM_EVENT_FLIP_COMPLETE, and checks that GETCRTC exposes the new framebuffer.
