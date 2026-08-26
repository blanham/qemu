/*
 * Explicit drm_file master-handoff, independent native-KMS modeset, and
 * event-driven page-flip witness for the pinned Linux VC4 fixture.
 *
 * The prerequisite modeset and page-flip witnesses run first on the card
 * drm_file opened by PID 1.  A bounded child then closes its inherited copy
 * and opens /dev/dri/card0 while PID 1 is still DRM master.  SET_MASTER on
 * that independent drm_file must fail with EBUSY.  Only after the child has
 * reported that precondition does PID 1 drop master and release the child to
 * retry SET_MASTER on the very same file.
 *
 * After explicit acquisition succeeds, the new drm_file must enumerate a
 * connected mode, create its own first framebuffer, program SETCRTC, and
 * verify that state through GETCRTC.  It then creates a second framebuffer,
 * queues an event-driven page flip, consumes DRM_EVENT_FLIP_COMPLETE, and
 * verifies the exact final scanout pixels.  Thus neither an inherited active
 * CRTC nor an implicitly assigned master can satisfy this witness.
 *
 * Opening card0 only after PID 1 dropped master would be weaker: Linux makes
 * the first primary-node opener master when no current master exists, and a
 * following SET_MASTER ioctl succeeds as a no-op.  The EBUSY-before-drop,
 * success-after-drop protocol rules out that implicit-open shortcut as well
 * as the inherited-file shortcut covered by linux-kms-pageflip-probe.inc.c.
 */

#include <fcntl.h>

#include "linux-kms-pageflip-probe.inc.c"

#define VC4_MASTER_REACQUIRE_CARD_PATH "/dev/dri/card0"
#define VC4_MASTER_REACQUIRE_TIMEOUT_SECONDS     35U
#define VC4_MASTER_REACQUIRE_VISUAL_HOLD_SECONDS 3U
#define VC4_MASTER_REACQUIRE_POLL_ATTEMPTS       20U
#define VC4_MASTER_REACQUIRE_POLL_TIMEOUT_MS     500
#define VC4_MASTER_REACQUIRE_HANDOFF_ATTEMPTS    70U
#define VC4_MASTER_REACQUIRE_READY_BYTE          UINT8_C(0x52)
#define VC4_MASTER_REACQUIRE_GO_BYTE             UINT8_C(0x47)
#define VC4_MASTER_REACQUIRE_USER_DATA \
    UINT64_C(0x5643344d41535452)

static int vc4_master_reacquire_fail(const char *stage)
{
    char message[224];
    int saved_errno = errno != 0 ? errno : EIO;

    snprintf(message, sizeof(message),
             "VC4_LINUX_KMS_MASTER_REACQUIRE_FAILED stage=%s errno=%d\n",
             stage, saved_errno);
    marker(message);
    errno = saved_errno;
    return -1;
}

static int vc4_master_reacquire_ioctl(int fd, unsigned long request,
                                      void *argument, const char *stage)
{
    int result;

    do {
        result = ioctl(fd, request, argument);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        return vc4_master_reacquire_fail(stage);
    }
    return 0;
}

static int vc4_master_reacquire_ioctl0(int fd, unsigned long request,
                                       const char *stage)
{
    int result;

    do {
        result = ioctl(fd, request, 0);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        return vc4_master_reacquire_fail(stage);
    }
    return 0;
}

static int vc4_master_reacquire_write_byte(int fd, uint8_t value,
                                           const char *stage)
{
    struct sigaction ignore = { 0 };
    struct sigaction previous = { 0 };
    ssize_t written;
    int saved_errno = 0;

    ignore.sa_handler = SIG_IGN;
    sigemptyset(&ignore.sa_mask);
    if (sigaction(SIGPIPE, &ignore, &previous) < 0) {
        return vc4_master_reacquire_fail("ignore-sigpipe");
    }

    do {
        written = write(fd, &value, sizeof(value));
    } while (written < 0 && errno == EINTR);
    if (written != (ssize_t)sizeof(value)) {
        saved_errno = written == 0 ? EPIPE : errno;
    }
    if (sigaction(SIGPIPE, &previous, NULL) < 0 && saved_errno == 0) {
        saved_errno = errno;
    }
    if (saved_errno != 0) {
        errno = saved_errno;
        return vc4_master_reacquire_fail(stage);
    }
    return 0;
}

static int vc4_master_reacquire_pipe(int descriptors[2],
                                     const char *stage)
{
    if (pipe(descriptors) < 0) {
        return vc4_master_reacquire_fail(stage);
    }
    for (unsigned int index = 0; index < 2; index++) {
        int flags = fcntl(descriptors[index], F_GETFD);

        if (flags < 0 ||
            fcntl(descriptors[index], F_SETFD, flags | FD_CLOEXEC) < 0) {
            int saved_errno = errno;

            (void)close(descriptors[0]);
            (void)close(descriptors[1]);
            descriptors[0] = -1;
            descriptors[1] = -1;
            errno = saved_errno;
            return vc4_master_reacquire_fail(stage);
        }
    }
    return 0;
}

static int vc4_master_reacquire_wait_byte(int fd, uint8_t expected,
                                          const char *stage)
{
    for (unsigned int attempt = 0;
         attempt < VC4_MASTER_REACQUIRE_HANDOFF_ATTEMPTS;
         attempt++) {
        struct pollfd descriptor = {
            .fd = fd,
            .events = POLLIN,
        };
        uint8_t value = 0;
        int poll_result;
        ssize_t length;

        do {
            poll_result = poll(&descriptor, 1,
                               VC4_MASTER_REACQUIRE_POLL_TIMEOUT_MS);
        } while (poll_result < 0 && errno == EINTR);
        if (poll_result < 0) {
            return vc4_master_reacquire_fail(stage);
        }
        if (poll_result == 0) {
            continue;
        }
        if (descriptor.revents & (POLLERR | POLLNVAL)) {
            errno = EIO;
            return vc4_master_reacquire_fail(stage);
        }
        if (!(descriptor.revents & (POLLIN | POLLHUP))) {
            continue;
        }

        do {
            length = read(fd, &value, sizeof(value));
        } while (length < 0 && errno == EINTR);
        if (length < 0 && errno == EAGAIN) {
            continue;
        }
        if (length != (ssize_t)sizeof(value)) {
            if (length == 0) {
                errno = EPIPE;
            }
            return vc4_master_reacquire_fail(stage);
        }
        if (value != expected) {
            errno = EPROTO;
            return vc4_master_reacquire_fail(stage);
        }
        return 0;
    }

    errno = ETIMEDOUT;
    return vc4_master_reacquire_fail(stage);
}

static int vc4_master_reacquire_expect_busy(int fd)
{
    int result;
    int saved_errno;

    errno = 0;
    do {
        result = ioctl(fd, DRM_IOCTL_SET_MASTER, 0);
    } while (result < 0 && errno == EINTR);
    if (result == 0) {
        /* Undo an unexpected implicit acquisition before reporting failure. */
        (void)ioctl(fd, DRM_IOCTL_DROP_MASTER, 0);
        errno = EPROTO;
        return vc4_master_reacquire_fail(
            "set-master-before-drop-unexpected-success");
    }

    saved_errno = errno;
    if (saved_errno != EBUSY) {
        errno = saved_errno;
        return vc4_master_reacquire_fail("set-master-before-drop");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK "
           "errno=%d\n", saved_errno);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK\n");
    errno = 0;
    return 0;
}

enum vc4_master_reacquire_buffer_kind {
    VC4_MASTER_REACQUIRE_MODESET_BUFFER,
    VC4_MASTER_REACQUIRE_PAGEFLIP_BUFFER,
};

struct vc4_master_reacquire_buffer {
    struct drm_mode_create_dumb create;
    struct drm_mode_fb_cmd2 framebuffer;
    void *mapping;
};

static void vc4_master_reacquire_fill_modeset_pattern(
    void *mapping, uint32_t pitch, uint32_t width, uint32_t height)
{
    for (uint32_t y = 0; y < height; y++) {
        uint32_t *row =
            (uint32_t *)((uint8_t *)mapping + (size_t)y * pitch);

        for (uint32_t x = 0; x < width; x++) {
            uint32_t red = width > 1 ? x * 255 / (width - 1) : 0;
            uint32_t green = height > 1 ? y * 255 / (height - 1) : 0;
            uint32_t blue = ((x / 24) ^ (y / 24)) & 1 ? 0x90 : 0x18;

            row[x] = (red << 16) | (green << 8) | blue;
        }
    }
}

static void vc4_master_reacquire_fill_pageflip_pattern(
    void *mapping, uint32_t pitch, uint32_t width, uint32_t height)
{
    for (uint32_t y = 0; y < height; y++) {
        uint32_t *row =
            (uint32_t *)((uint8_t *)mapping + (size_t)y * pitch);

        for (uint32_t x = 0; x < width; x++) {
            uint32_t checker = ((x / 40) ^ (y / 24)) & 1;
            uint32_t red = width > 1 ?
                           255 - x * 255 / (width - 1) : 0xff;
            uint32_t green = checker ? 0xe0 : 0x24;
            uint32_t blue = height > 1 ?
                            y * 255 / (height - 1) : 0;

            row[x] = (red << 16) | (green << 8) | blue;
        }
    }
}

static int vc4_master_reacquire_create_buffer(
    int fd, uint32_t width, uint32_t height,
    enum vc4_master_reacquire_buffer_kind kind,
    struct vc4_master_reacquire_buffer *buffer)
{
    struct drm_mode_map_dumb map = { 0 };
    const bool modeset = kind == VC4_MASTER_REACQUIRE_MODESET_BUFFER;
    const char *create_stage = modeset ?
        "create-independent-modeset-dumb" : "create-pageflip-dumb";
    const char *map_stage = modeset ?
        "map-independent-modeset-dumb" : "map-pageflip-dumb";
    const char *mmap_stage = modeset ?
        "mmap-independent-modeset-dumb" : "mmap-pageflip-dumb";
    const char *addfb_stage = modeset ?
        "addfb2-independent-modeset" : "addfb2-pageflip";

    if (buffer == NULL || width == 0 || height == 0) {
        errno = EINVAL;
        return vc4_master_reacquire_fail("invalid-buffer-request");
    }
    memset(buffer, 0, sizeof(*buffer));
    buffer->mapping = MAP_FAILED;
    buffer->create.width = width;
    buffer->create.height = height;
    buffer->create.bpp = 32;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_CREATE_DUMB, &buffer->create,
            create_stage) < 0) {
        return -1;
    }
    if (buffer->create.handle == 0 || buffer->create.pitch == 0 ||
        buffer->create.size == 0) {
        errno = EIO;
        return vc4_master_reacquire_fail(
            modeset ? "independent-modeset-dumb-invalid" :
                      "pageflip-dumb-invalid");
    }
    if (modeset) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK "
               "handle=%u pitch=%u size=%llu\n",
               buffer->create.handle, buffer->create.pitch,
               (unsigned long long)buffer->create.size);
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK\n");
    } else {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK "
               "handle=%u pitch=%u size=%llu\n",
               buffer->create.handle, buffer->create.pitch,
               (unsigned long long)buffer->create.size);
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK\n");
    }

    map.handle = buffer->create.handle;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map,
                                   map_stage) < 0) {
        return -1;
    }
    buffer->mapping = mmap(NULL, buffer->create.size,
                           PROT_READ | PROT_WRITE, MAP_SHARED,
                           fd, (off_t)map.offset);
    if (buffer->mapping == MAP_FAILED) {
        return vc4_master_reacquire_fail(mmap_stage);
    }
    if (modeset) {
        vc4_master_reacquire_fill_modeset_pattern(
            buffer->mapping, buffer->create.pitch, width, height);
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK\n");
    } else {
        vc4_master_reacquire_fill_pageflip_pattern(
            buffer->mapping, buffer->create.pitch, width, height);
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK\n");
    }
    __sync_synchronize();

    buffer->framebuffer.width = width;
    buffer->framebuffer.height = height;
    buffer->framebuffer.pixel_format = VC4_MODESET_DRM_FORMAT_XRGB8888;
    buffer->framebuffer.handles[0] = buffer->create.handle;
    buffer->framebuffer.pitches[0] = buffer->create.pitch;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_ADDFB2, &buffer->framebuffer,
            addfb_stage) < 0) {
        return -1;
    }
    if (buffer->framebuffer.fb_id == 0) {
        errno = EIO;
        return vc4_master_reacquire_fail(
            modeset ? "independent-modeset-fb-invalid" :
                      "pageflip-fb-invalid");
    }
    if (modeset) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK "
               "fb=%u\n", buffer->framebuffer.fb_id);
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK\n");
    } else {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK fb=%u\n",
               buffer->framebuffer.fb_id);
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK\n");
    }
    return 0;
}

static int vc4_master_reacquire_unmap_buffer(
    struct vc4_master_reacquire_buffer *buffer, const char *stage)
{
    if (buffer == NULL || buffer->mapping == MAP_FAILED) {
        return 0;
    }
    if (munmap(buffer->mapping, buffer->create.size) < 0) {
        return vc4_master_reacquire_fail(stage);
    }
    buffer->mapping = MAP_FAILED;
    return 0;
}

static int vc4_master_reacquire_wait_event(int fd, uint32_t crtc_id,
                                           uint64_t expected_user_data)
{
    uint64_t event_words[512];
    uint8_t *event_bytes = (uint8_t *)event_words;

    for (unsigned int attempt = 0;
         attempt < VC4_MASTER_REACQUIRE_POLL_ATTEMPTS;
         attempt++) {
        struct pollfd descriptor = {
            .fd = fd,
            .events = POLLIN,
        };
        int poll_result;
        ssize_t length;
        size_t offset = 0;

        do {
            poll_result = poll(&descriptor, 1,
                               VC4_MASTER_REACQUIRE_POLL_TIMEOUT_MS);
        } while (poll_result < 0 && errno == EINTR);
        if (poll_result < 0) {
            return vc4_master_reacquire_fail("poll-event");
        }
        if (poll_result == 0) {
            continue;
        }
        if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            errno = EIO;
            return vc4_master_reacquire_fail("poll-event-state");
        }
        if (!(descriptor.revents & POLLIN)) {
            continue;
        }

        do {
            length = read(fd, event_words, sizeof(event_words));
        } while (length < 0 && errno == EINTR);
        if (length < 0 && errno == EAGAIN) {
            continue;
        }
        if (length <= 0) {
            if (length == 0) {
                errno = EIO;
            }
            return vc4_master_reacquire_fail("read-event");
        }

        while (offset < (size_t)length) {
            struct drm_event event;

            if ((size_t)length - offset < sizeof(event)) {
                errno = EPROTO;
                return vc4_master_reacquire_fail("event-header-short");
            }
            memcpy(&event, event_bytes + offset, sizeof(event));
            if (event.length < sizeof(event) ||
                event.length > (size_t)length - offset) {
                errno = EPROTO;
                return vc4_master_reacquire_fail("event-length");
            }

            if (event.type == DRM_EVENT_FLIP_COMPLETE &&
                event.length >= sizeof(struct drm_event_vblank)) {
                struct drm_event_vblank vblank;

                memcpy(&vblank, event_bytes + offset, sizeof(vblank));
                report("VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT "
                       "type=%u length=%u user=0x%016llx "
                       "sequence=%u crtc=%u\n",
                       vblank.base.type, vblank.base.length,
                       (unsigned long long)vblank.user_data,
                       vblank.sequence, vblank.crtc_id);
                if (vblank.user_data == expected_user_data &&
                    (vblank.crtc_id == 0 || vblank.crtc_id == crtc_id)) {
                    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK\n");
                    return 0;
                }
            }

            offset += event.length;
        }
    }

    errno = ETIMEDOUT;
    return vc4_master_reacquire_fail("wait-event-timeout");
}

static int vc4_master_reacquire_run(int inherited_master_fd,
                                    int ready_fd, int gate_fd)
{
    struct vc4_modeset_selection selection = { 0 };
    struct drm_mode_crtc baseline = { 0 };
    struct drm_mode_crtc modeset = { 0 };
    struct drm_mode_crtc current = { 0 };
    struct drm_mode_crtc_page_flip page_flip = { 0 };
    struct vc4_master_reacquire_buffer modeset_buffer = {
        .mapping = MAP_FAILED,
    };
    struct vc4_master_reacquire_buffer pageflip_buffer = {
        .mapping = MAP_FAILED,
    };
    uint32_t connector_id;
    int fd;

    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_START\n");
    if (inherited_master_fd < 0 || ready_fd < 0 || gate_fd < 0) {
        errno = EBADF;
        return vc4_master_reacquire_fail("invalid-handoff-fd");
    }

    if (close(inherited_master_fd) < 0) {
        return vc4_master_reacquire_fail("close-inherited-fd");
    }
    errno = 0;
    if (fcntl(inherited_master_fd, F_GETFD) != -1 || errno != EBADF) {
        errno = EPROTO;
        return vc4_master_reacquire_fail("verify-inherited-fd-closed");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED\n");

    do {
        fd = open(VC4_MASTER_REACQUIRE_CARD_PATH,
                  O_RDWR | O_CLOEXEC | O_NONBLOCK);
    } while (fd < 0 && errno == EINTR);
    if (fd < 0) {
        return vc4_master_reacquire_fail("reopen-card0-before-drop");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK path=%s fd=%d\n",
           VC4_MASTER_REACQUIRE_CARD_PATH, fd);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK\n");

    if (vc4_master_reacquire_expect_busy(fd) < 0) {
        (void)close(fd);
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_READY\n");
    if (vc4_master_reacquire_write_byte(
            ready_fd, VC4_MASTER_REACQUIRE_READY_BYTE,
            "signal-handoff-ready") < 0) {
        (void)close(fd);
        return -1;
    }
    (void)close(ready_fd);

    if (vc4_master_reacquire_wait_byte(
            gate_fd, VC4_MASTER_REACQUIRE_GO_BYTE,
            "wait-original-master-drop") < 0) {
        (void)close(fd);
        return -1;
    }
    (void)close(gate_fd);

    if (vc4_master_reacquire_ioctl0(fd, DRM_IOCTL_SET_MASTER,
                                    "set-master-after-drop") < 0) {
        (void)close(fd);
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK\n");

    if (vc4_modeset_select(fd, &selection) < 0) {
        (void)vc4_master_reacquire_fail("select-connected-mode");
        goto mastered_fail;
    }
    if (selection.connector_id == 0 || selection.crtc_id == 0 ||
        selection.mode.hdisplay == 0 || selection.mode.vdisplay == 0) {
        errno = ENODEV;
        (void)vc4_master_reacquire_fail("selection-invalid");
        goto mastered_fail;
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK "
           "connector=%u crtc=%u mode=%ux%u clock=%u\n",
           selection.connector_id, selection.crtc_id,
           selection.mode.hdisplay, selection.mode.vdisplay,
           selection.mode.clock);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK\n");

    baseline.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &baseline,
                                   "getcrtc-baseline") < 0) {
        goto mastered_fail;
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK "
           "crtc=%u fb=%u mode_valid=%u mode=%ux%u\n",
           baseline.crtc_id, baseline.fb_id, baseline.mode_valid,
           baseline.mode.hdisplay, baseline.mode.vdisplay);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK\n");

    if (vc4_master_reacquire_create_buffer(
            fd, selection.mode.hdisplay, selection.mode.vdisplay,
            VC4_MASTER_REACQUIRE_MODESET_BUFFER,
            &modeset_buffer) < 0) {
        goto mastered_fail;
    }

    connector_id = selection.connector_id;
    modeset.crtc_id = selection.crtc_id;
    modeset.fb_id = modeset_buffer.framebuffer.fb_id;
    modeset.set_connectors_ptr = (uintptr_t)&connector_id;
    modeset.count_connectors = 1;
    modeset.mode = selection.mode;
    modeset.mode_valid = 1;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START\n");
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_SETCRTC, &modeset,
            "setcrtc-independent-file") < 0) {
        goto mastered_fail;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK\n");

    current.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETCRTC, &current,
            "getcrtc-after-independent-setcrtc") < 0) {
        goto mastered_fail;
    }
    if (current.fb_id != modeset_buffer.framebuffer.fb_id ||
        !current.mode_valid ||
        current.mode.hdisplay != selection.mode.hdisplay ||
        current.mode.vdisplay != selection.mode.vdisplay) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MISMATCH "
               "expected_fb=%u actual_fb=%u mode_valid=%u "
               "expected_mode=%ux%u actual_mode=%ux%u\n",
               modeset_buffer.framebuffer.fb_id, current.fb_id,
               current.mode_valid,
               selection.mode.hdisplay, selection.mode.vdisplay,
               current.mode.hdisplay, current.mode.vdisplay);
        errno = EIO;
        (void)vc4_master_reacquire_fail(
            "independent-modeset-current-fb-mismatch");
        goto mastered_fail;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK\n");
    if (vc4_master_reacquire_unmap_buffer(
            &modeset_buffer, "munmap-independent-modeset-dumb") < 0) {
        goto mastered_fail;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK\n");

    if (vc4_master_reacquire_create_buffer(
            fd, selection.mode.hdisplay, selection.mode.vdisplay,
            VC4_MASTER_REACQUIRE_PAGEFLIP_BUFFER,
            &pageflip_buffer) < 0) {
        goto mastered_fail;
    }

    page_flip.crtc_id = selection.crtc_id;
    page_flip.fb_id = pageflip_buffer.framebuffer.fb_id;
    page_flip.flags = DRM_MODE_PAGE_FLIP_EVENT;
    page_flip.user_data = VC4_MASTER_REACQUIRE_USER_DATA;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START\n");
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &page_flip,
                                   "page-flip-ioctl") < 0) {
        goto mastered_fail;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED\n");

    if (vc4_master_reacquire_wait_event(
            fd, selection.crtc_id,
            VC4_MASTER_REACQUIRE_USER_DATA) < 0) {
        goto mastered_fail;
    }

    memset(&current, 0, sizeof(current));
    current.crtc_id = selection.crtc_id;
    if (vc4_master_reacquire_ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &current,
                                   "getcrtc-after-pageflip") < 0) {
        goto mastered_fail;
    }
    if (current.fb_id != pageflip_buffer.framebuffer.fb_id) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_FB_MISMATCH "
               "expected=%u actual=%u\n",
               pageflip_buffer.framebuffer.fb_id, current.fb_id);
        errno = EIO;
        (void)vc4_master_reacquire_fail("current-fb-mismatch");
        goto mastered_fail;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK\n");
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY\n");
    sleep(VC4_MASTER_REACQUIRE_VISUAL_HOLD_SECONDS);

    if (vc4_master_reacquire_unmap_buffer(
            &pageflip_buffer, "munmap-pageflip-dumb") < 0) {
        goto mastered_fail;
    }
    if (vc4_master_reacquire_ioctl0(fd, DRM_IOCTL_DROP_MASTER,
                                    "drop-master-new-file") < 0) {
        goto mastered_fail;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED\n");
    if (close(fd) < 0) {
        return vc4_master_reacquire_fail("close-reopened-fd");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_OK\n");
    return 0;

mastered_fail:
    {
        int saved_errno = errno != 0 ? errno : EIO;
        int drop_result;
        int drop_errno = 0;
        int close_result;
        int close_errno = 0;

        if (modeset_buffer.mapping != MAP_FAILED) {
            (void)munmap(modeset_buffer.mapping, modeset_buffer.create.size);
            modeset_buffer.mapping = MAP_FAILED;
        }
        if (pageflip_buffer.mapping != MAP_FAILED) {
            (void)munmap(pageflip_buffer.mapping, pageflip_buffer.create.size);
            pageflip_buffer.mapping = MAP_FAILED;
        }
        do {
            drop_result = ioctl(fd, DRM_IOCTL_DROP_MASTER, 0);
        } while (drop_result < 0 && errno == EINTR);
        if (drop_result < 0) {
            drop_errno = errno;
        }
        close_result = close(fd);
        if (close_result < 0) {
            close_errno = errno;
        }
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_FAILURE_CLEANUP "
               "drop=%d drop_errno=%d close=%d close_errno=%d\n",
               drop_result, drop_errno, close_result, close_errno);
        errno = saved_errno;
        return -1;
    }
}

static int vc4_master_reacquire_restore_original(int master_fd)
{
    if (vc4_master_reacquire_ioctl0(master_fd, DRM_IOCTL_SET_MASTER,
                                    "restore-original-master") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED\n");
    return 0;
}

static bool vc4_master_reacquire_child_ok(int status)
{
    if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
        return true;
    }
    if (WIFEXITED(status)) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_EXIT status=%d\n",
               WEXITSTATUS(status));
        return false;
    }
    if (WIFSIGNALED(status)) {
        int signal = WTERMSIG(status);

        report("VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_SIGNAL signal=%d\n",
               signal);
        if (signal == SIGALRM) {
            marker("VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT\n");
        }
    }
    return false;
}

static int vc4_kms_master_reacquire_supervise(int master_fd)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MODESET_POLL_NS,
    };
    int ready_pipe[2] = { -1, -1 };
    int gate_pipe[2] = { -1, -1 };
    pid_t child = -1;
    bool original_dropped = false;
    bool child_reaped = false;
    int child_status = 0;
    int failure_errno = 0;
    int result = -1;

    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_START\n");
    if (master_fd < 0) {
        errno = EBADF;
        return vc4_master_reacquire_fail("invalid-original-fd");
    }
    if (vc4_master_reacquire_pipe(ready_pipe, "ready-pipe") < 0) {
        return -1;
    }
    if (vc4_master_reacquire_pipe(gate_pipe, "gate-pipe") < 0) {
        failure_errno = errno;
        goto out;
    }

    child = fork();
    if (child < 0) {
        failure_errno = errno;
        (void)vc4_master_reacquire_fail("fork");
        goto out;
    }
    if (child == 0) {
        int child_result;

        (void)close(ready_pipe[0]);
        (void)close(gate_pipe[1]);
        alarm(VC4_MASTER_REACQUIRE_TIMEOUT_SECONDS - 2);
        child_result = vc4_master_reacquire_run(
            master_fd, ready_pipe[1], gate_pipe[0]);
        _exit(child_result == 0 ? 0 : 1);
    }

    (void)close(ready_pipe[1]);
    ready_pipe[1] = -1;
    (void)close(gate_pipe[0]);
    gate_pipe[0] = -1;

    if (vc4_master_reacquire_wait_byte(
            ready_pipe[0], VC4_MASTER_REACQUIRE_READY_BYTE,
            "wait-handoff-ready") < 0) {
        failure_errno = errno;
        goto out;
    }
    (void)close(ready_pipe[0]);
    ready_pipe[0] = -1;

    if (vc4_master_reacquire_ioctl0(master_fd, DRM_IOCTL_DROP_MASTER,
                                    "drop-original-master") < 0) {
        failure_errno = errno;
        goto out;
    }
    original_dropped = true;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED\n");

    if (vc4_master_reacquire_write_byte(
            gate_pipe[1], VC4_MASTER_REACQUIRE_GO_BYTE,
            "release-handoff-gate") < 0) {
        failure_errno = errno;
        goto out;
    }
    (void)close(gate_pipe[1]);
    gate_pipe[1] = -1;

    for (unsigned int iteration = 0;
         iteration < VC4_MASTER_REACQUIRE_TIMEOUT_SECONDS * 10;
         iteration++) {
        pid_t waited;

        do {
            waited = waitpid(child, &child_status, WNOHANG);
        } while (waited < 0 && errno == EINTR);
        if (waited == child) {
            child_reaped = true;
            break;
        }
        if (waited < 0) {
            failure_errno = errno;
            (void)vc4_master_reacquire_fail("waitpid");
            goto out;
        }
        nanosleep(&delay, NULL);
    }

    if (!child_reaped) {
        failure_errno = ETIMEDOUT;
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT\n");
        goto out;
    }
    if (vc4_master_reacquire_child_ok(child_status)) {
        result = 0;
    } else if (WIFSIGNALED(child_status) &&
               WTERMSIG(child_status) == SIGALRM) {
        failure_errno = ETIMEDOUT;
    } else {
        failure_errno = ECHILD;
    }

out:
    if (ready_pipe[0] >= 0) {
        (void)close(ready_pipe[0]);
    }
    if (ready_pipe[1] >= 0) {
        (void)close(ready_pipe[1]);
    }
    if (gate_pipe[0] >= 0) {
        (void)close(gate_pipe[0]);
    }
    if (gate_pipe[1] >= 0) {
        (void)close(gate_pipe[1]);
    }

    if (!child_reaped && child > 0) {
        pid_t waited;

        do {
            waited = waitpid(child, &child_status, WNOHANG);
        } while (waited < 0 && errno == EINTR);
        if (waited == child) {
            child_reaped = true;
            (void)vc4_master_reacquire_child_ok(child_status);
        } else {
            (void)kill(child, SIGKILL);
            do {
                waited = waitpid(child, &child_status, 0);
            } while (waited < 0 && errno == EINTR);
            child_reaped = waited == child;
            if (child_reaped) {
                (void)vc4_master_reacquire_child_ok(child_status);
            }
        }
    }

    if (original_dropped) {
        if (vc4_master_reacquire_restore_original(master_fd) < 0) {
            marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_FAILED\n");
            return -1;
        }
    } else if (child > 0 &&
               vc4_master_reacquire_ioctl0(
                   master_fd, DRM_IOCTL_SET_MASTER,
                   "ensure-original-master") < 0) {
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_FAILED\n");
        return -1;
    }

    if (result == 0) {
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_ORDER_OK\n");
        marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK\n");
        return 0;
    }

    if (failure_errno == 0) {
        failure_errno = EIO;
    }
    errno = failure_errno;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_FAILED\n");
    return -1;
}
