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
#include <stdlib.h>

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
#define VC4_MASTER_REACQUIRE_ATOMIC_USER_DATA \
    UINT64_C(0x56433441544f4d43)
#define VC4_MASTER_REACQUIRE_MAX_CRTCS      16U
#define VC4_MASTER_REACQUIRE_MAX_PLANES     64U
#define VC4_MASTER_REACQUIRE_MAX_PROPERTIES 96U
#define VC4_MASTER_REACQUIRE_PRIMARY_PLANE  UINT64_C(1)

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
    VC4_MASTER_REACQUIRE_ATOMIC_BUFFER,
};

enum vc4_master_reacquire_event_kind {
    VC4_MASTER_REACQUIRE_PAGEFLIP_EVENT,
    VC4_MASTER_REACQUIRE_ATOMIC_EVENT,
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
            uint32_t checker = ((x / 28) ^ (y / 20)) & 1;
            uint32_t red = checker ? 0x32 : 0xd8;
            uint32_t green = width > 1 ?
                             255 - x * 255 / (width - 1) : 0xff;
            uint32_t blue = height > 1 ?
                            255 - y * 255 / (height - 1) : 0xff;

            row[x] = (red << 16) | (green << 8) | blue;
        }
    }
}

static void vc4_master_reacquire_fill_atomic_pattern(
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
    const char *create_stage;
    const char *map_stage;
    const char *mmap_stage;
    const char *addfb_stage;
    const char *invalid_dumb_stage;
    const char *invalid_fb_stage;
    const char *dumb_marker;
    const char *map_marker;
    const char *fb_marker;
    const char *label;

    switch (kind) {
    case VC4_MASTER_REACQUIRE_MODESET_BUFFER:
        create_stage = "create-independent-modeset-dumb";
        map_stage = "map-independent-modeset-dumb";
        mmap_stage = "mmap-independent-modeset-dumb";
        addfb_stage = "addfb2-independent-modeset";
        invalid_dumb_stage = "independent-modeset-dumb-invalid";
        invalid_fb_stage = "independent-modeset-fb-invalid";
        dumb_marker =
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK\n";
        map_marker =
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK\n";
        fb_marker =
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK\n";
        label = "MODESET";
        break;
    case VC4_MASTER_REACQUIRE_PAGEFLIP_BUFFER:
        create_stage = "create-pageflip-dumb";
        map_stage = "map-pageflip-dumb";
        mmap_stage = "mmap-pageflip-dumb";
        addfb_stage = "addfb2-pageflip";
        invalid_dumb_stage = "pageflip-dumb-invalid";
        invalid_fb_stage = "pageflip-fb-invalid";
        dumb_marker = "VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK\n";
        map_marker = "VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK\n";
        fb_marker = "VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK\n";
        label = "PAGEFLIP";
        break;
    case VC4_MASTER_REACQUIRE_ATOMIC_BUFFER:
        create_stage = "create-atomic-dumb";
        map_stage = "map-atomic-dumb";
        mmap_stage = "mmap-atomic-dumb";
        addfb_stage = "addfb2-atomic";
        invalid_dumb_stage = "atomic-dumb-invalid";
        invalid_fb_stage = "atomic-fb-invalid";
        dumb_marker =
            "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_DUMB_OK\n";
        map_marker =
            "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_MAP_OK\n";
        fb_marker =
            "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_FB_OK\n";
        label = "ATOMIC";
        break;
    default:
        errno = EINVAL;
        return vc4_master_reacquire_fail("invalid-buffer-kind");
    }

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
        return vc4_master_reacquire_fail(invalid_dumb_stage);
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_%s_DUMB "
           "handle=%u pitch=%u size=%llu\n",
           label, buffer->create.handle, buffer->create.pitch,
           (unsigned long long)buffer->create.size);
    marker(dumb_marker);

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
    switch (kind) {
    case VC4_MASTER_REACQUIRE_MODESET_BUFFER:
        vc4_master_reacquire_fill_modeset_pattern(
            buffer->mapping, buffer->create.pitch, width, height);
        break;
    case VC4_MASTER_REACQUIRE_PAGEFLIP_BUFFER:
        vc4_master_reacquire_fill_pageflip_pattern(
            buffer->mapping, buffer->create.pitch, width, height);
        break;
    case VC4_MASTER_REACQUIRE_ATOMIC_BUFFER:
        vc4_master_reacquire_fill_atomic_pattern(
            buffer->mapping, buffer->create.pitch, width, height);
        break;
    default:
        abort();
    }
    __sync_synchronize();
    marker(map_marker);

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
        return vc4_master_reacquire_fail(invalid_fb_stage);
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_%s_FB fb=%u\n",
           label, buffer->framebuffer.fb_id);
    marker(fb_marker);
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
                const bool atomic = expected_user_data ==
                    VC4_MASTER_REACQUIRE_ATOMIC_USER_DATA;

                report("VC4_LINUX_KMS_MASTER_REACQUIRE_%s_EVENT "
                       "type=%u length=%u user=0x%016llx "
                       "sequence=%u crtc=%u\n",
                       atomic ? "ATOMIC" : "PAGEFLIP",
                       vblank.base.type, vblank.base.length,
                       (unsigned long long)vblank.user_data,
                       vblank.sequence, vblank.crtc_id);
                if (vblank.user_data == expected_user_data &&
                    (vblank.crtc_id == 0 || vblank.crtc_id == crtc_id)) {
                    marker(atomic ?
                        "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_EVENT_OK\n" :
                        "VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK\n");
                    return 0;
                }
            }

            offset += event.length;
        }
    }

    errno = ETIMEDOUT;
    return vc4_master_reacquire_fail("wait-event-timeout");
}


struct vc4_master_reacquire_plane_properties {
    bool type_found;
    bool fb_id_found;
    bool crtc_id_found;
    uint64_t type_value;
    uint32_t fb_id_property;
    uint64_t fb_id_value;
    uint64_t crtc_id_value;
};

struct vc4_master_reacquire_primary_plane {
    uint32_t plane_id;
    uint32_t fb_id_property;
};

static int vc4_master_reacquire_crtc_index(int fd, uint32_t crtc_id,
                                            uint32_t *crtc_index)
{
    struct drm_mode_card_res resources = { 0 };
    uint32_t *crtc_ids = NULL;
    int result = -1;

    if (crtc_id == 0 || crtc_index == NULL) {
        errno = EINVAL;
        return vc4_master_reacquire_fail("invalid-crtc-index-request");
    }
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETRESOURCES, &resources,
            "atomic-getresources-count") < 0) {
        return -1;
    }
    if (resources.count_crtcs == 0 ||
        resources.count_crtcs > VC4_MASTER_REACQUIRE_MAX_CRTCS) {
        errno = resources.count_crtcs == 0 ? ENODEV : EOVERFLOW;
        return vc4_master_reacquire_fail("atomic-crtc-count");
    }

    crtc_ids = calloc(resources.count_crtcs, sizeof(*crtc_ids));
    if (crtc_ids == NULL) {
        return vc4_master_reacquire_fail("atomic-allocate-crtcs");
    }
    /*
     * The count-only call also returns the sizes of the framebuffer,
     * connector, and encoder arrays.  We are only providing storage for
     * CRTC IDs here, so advertise zero capacity for every unused array.
     * Otherwise drm_mode_getresources() quite correctly attempts to copy
     * those IDs through our still-NULL pointers and returns EFAULT.
     */
    resources.count_fbs = 0;
    resources.count_connectors = 0;
    resources.count_encoders = 0;
    resources.crtc_id_ptr = (uintptr_t)crtc_ids;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETRESOURCES, &resources,
            "atomic-getresources-ids") < 0) {
        goto out;
    }
    for (uint32_t index = 0; index < resources.count_crtcs; index++) {
        if (crtc_ids[index] == crtc_id) {
            *crtc_index = index;
            result = 0;
            goto out;
        }
    }

    errno = ENODEV;
    (void)vc4_master_reacquire_fail("atomic-crtc-index-not-found");

out:
    free(crtc_ids);
    return result;
}

static int vc4_master_reacquire_plane_properties(
    int fd, uint32_t plane_id,
    struct vc4_master_reacquire_plane_properties *result)
{
    struct drm_mode_obj_get_properties properties = {
        .obj_id = plane_id,
        .obj_type = DRM_MODE_OBJECT_PLANE,
    };
    uint32_t *property_ids = NULL;
    uint64_t *property_values = NULL;
    int status = -1;

    if (plane_id == 0 || result == NULL) {
        errno = EINVAL;
        return vc4_master_reacquire_fail(
            "invalid-plane-property-request");
    }
    memset(result, 0, sizeof(*result));
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &properties,
            "atomic-get-plane-property-count") < 0) {
        return -1;
    }
    if (properties.count_props == 0 ||
        properties.count_props > VC4_MASTER_REACQUIRE_MAX_PROPERTIES) {
        errno = properties.count_props == 0 ? ENODEV : EOVERFLOW;
        return vc4_master_reacquire_fail("atomic-plane-property-count");
    }

    property_ids = calloc(properties.count_props, sizeof(*property_ids));
    property_values = calloc(properties.count_props,
                             sizeof(*property_values));
    if (property_ids == NULL || property_values == NULL) {
        (void)vc4_master_reacquire_fail(
            "atomic-allocate-plane-properties");
        goto out;
    }
    properties.props_ptr = (uintptr_t)property_ids;
    properties.prop_values_ptr = (uintptr_t)property_values;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &properties,
            "atomic-get-plane-properties") < 0) {
        goto out;
    }

    for (uint32_t index = 0; index < properties.count_props; index++) {
        struct drm_mode_get_property property = {
            .prop_id = property_ids[index],
        };

        if (vc4_master_reacquire_ioctl(
                fd, DRM_IOCTL_MODE_GETPROPERTY, &property,
                "atomic-get-property-metadata") < 0) {
            goto out;
        }
        property.name[DRM_PROP_NAME_LEN - 1] = '\0';
        if (strcmp(property.name, "type") == 0) {
            result->type_found = true;
            result->type_value = property_values[index];
        } else if (strcmp(property.name, "FB_ID") == 0) {
            result->fb_id_found = true;
            result->fb_id_property = property.prop_id;
            result->fb_id_value = property_values[index];
        } else if (strcmp(property.name, "CRTC_ID") == 0) {
            result->crtc_id_found = true;
            result->crtc_id_value = property_values[index];
        }
    }

    if (!result->type_found || !result->fb_id_found ||
        !result->crtc_id_found) {
        errno = ENOENT;
        (void)vc4_master_reacquire_fail(
            "atomic-required-plane-property-missing");
        goto out;
    }
    status = 0;

out:
    free(property_values);
    free(property_ids);
    return status;
}


static int vc4_master_reacquire_object_property(
    int fd, uint32_t object_id, uint32_t object_type, const char *name,
    uint32_t *property_id, uint64_t *property_value)
{
    struct drm_mode_obj_get_properties properties = {
        .obj_id = object_id,
        .obj_type = object_type,
    };
    uint32_t *property_ids = NULL;
    uint64_t *property_values = NULL;
    int status = -1;

    if (object_id == 0 || name == NULL || property_id == NULL ||
        property_value == NULL) {
        errno = EINVAL;
        return vc4_master_reacquire_fail(
            "invalid-object-property-request");
    }
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &properties,
            "atomic-get-object-property-count") < 0) {
        return -1;
    }
    if (properties.count_props == 0 ||
        properties.count_props > VC4_MASTER_REACQUIRE_MAX_PROPERTIES) {
        errno = properties.count_props == 0 ? ENODEV : EOVERFLOW;
        return vc4_master_reacquire_fail("atomic-object-property-count");
    }

    property_ids = calloc(properties.count_props, sizeof(*property_ids));
    property_values = calloc(properties.count_props,
                             sizeof(*property_values));
    if (property_ids == NULL || property_values == NULL) {
        (void)vc4_master_reacquire_fail(
            "atomic-allocate-object-properties");
        goto out;
    }
    properties.props_ptr = (uintptr_t)property_ids;
    properties.prop_values_ptr = (uintptr_t)property_values;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_OBJ_GETPROPERTIES, &properties,
            "atomic-get-object-properties") < 0) {
        goto out;
    }

    for (uint32_t index = 0; index < properties.count_props; index++) {
        struct drm_mode_get_property property = {
            .prop_id = property_ids[index],
        };

        if (vc4_master_reacquire_ioctl(
                fd, DRM_IOCTL_MODE_GETPROPERTY, &property,
                "atomic-get-object-property-metadata") < 0) {
            goto out;
        }
        property.name[DRM_PROP_NAME_LEN - 1] = '\0';
        if (strcmp(property.name, name) == 0) {
            *property_id = property.prop_id;
            *property_value = property_values[index];
            status = 0;
            goto out;
        }
    }

    errno = ENOENT;
    (void)vc4_master_reacquire_fail("atomic-object-property-not-found");

out:
    free(property_values);
    free(property_ids);
    return status;
}

static int vc4_master_reacquire_primary_plane(
    int fd, uint32_t crtc_id, uint32_t expected_fb_id,
    struct vc4_master_reacquire_primary_plane *result)
{
    struct drm_set_client_cap capability = {
        .capability = DRM_CLIENT_CAP_ATOMIC,
        .value = 1,
    };
    struct drm_mode_get_plane_res resources = { 0 };
    uint32_t *plane_ids = NULL;
    uint32_t crtc_index = 0;
    int status = -1;

    if (result == NULL || crtc_id == 0 || expected_fb_id == 0) {
        errno = EINVAL;
        return vc4_master_reacquire_fail(
            "invalid-primary-plane-request");
    }
    memset(result, 0, sizeof(*result));
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_SET_CLIENT_CAP, &capability,
            "atomic-enable-client-cap") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CAPS_OK\n");

    if (vc4_master_reacquire_crtc_index(
            fd, crtc_id, &crtc_index) < 0) {
        return -1;
    }
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETPLANERESOURCES, &resources,
            "atomic-get-plane-count") < 0) {
        return -1;
    }
    if (resources.count_planes == 0 ||
        resources.count_planes > VC4_MASTER_REACQUIRE_MAX_PLANES) {
        errno = resources.count_planes == 0 ? ENODEV : EOVERFLOW;
        return vc4_master_reacquire_fail("atomic-plane-count");
    }

    plane_ids = calloc(resources.count_planes, sizeof(*plane_ids));
    if (plane_ids == NULL) {
        return vc4_master_reacquire_fail("atomic-allocate-planes");
    }
    resources.plane_id_ptr = (uintptr_t)plane_ids;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETPLANERESOURCES, &resources,
            "atomic-get-plane-ids") < 0) {
        goto out;
    }

    for (uint32_t index = 0; index < resources.count_planes; index++) {
        struct drm_mode_get_plane plane = {
            .plane_id = plane_ids[index],
        };
        struct vc4_master_reacquire_plane_properties properties;

        if (vc4_master_reacquire_ioctl(
                fd, DRM_IOCTL_MODE_GETPLANE, &plane,
                "atomic-get-plane") < 0) {
            goto out;
        }
        if (crtc_index >= 32 ||
            !(plane.possible_crtcs & (UINT32_C(1) << crtc_index))) {
            continue;
        }
        if (vc4_master_reacquire_plane_properties(
                fd, plane.plane_id, &properties) < 0) {
            goto out;
        }
        if (properties.type_value !=
                VC4_MASTER_REACQUIRE_PRIMARY_PLANE ||
            plane.crtc_id != crtc_id ||
            properties.crtc_id_value != crtc_id ||
            plane.fb_id != expected_fb_id ||
            properties.fb_id_value != expected_fb_id) {
            continue;
        }

        result->plane_id = plane.plane_id;
        result->fb_id_property = properties.fb_id_property;
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_PRIMARY "
               "plane=%u crtc=%u fb=%u fb_prop=%u crtc_index=%u\n",
               result->plane_id, crtc_id, expected_fb_id,
               result->fb_id_property, crtc_index);
        marker(
            "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_PRIMARY_PLANE_OK\n");
        status = 0;
        goto out;
    }

    errno = ENODEV;
    (void)vc4_master_reacquire_fail("atomic-primary-plane-not-found");

out:
    free(plane_ids);
    return status;
}

static int vc4_master_reacquire_atomic_swap(
    int fd, const struct vc4_modeset_selection *selection,
    uint32_t current_fb_id,
    struct vc4_master_reacquire_buffer *atomic_buffer)
{
    struct vc4_master_reacquire_primary_plane primary = { 0 };
    struct drm_mode_crtc current = { 0 };
    uint32_t active_property = 0;
    uint64_t active_value = 0;
    uint32_t object_ids[2];
    uint32_t property_counts[2] = { 1, 1 };
    uint32_t property_ids[2];
    uint64_t property_values[2];
    struct drm_mode_atomic atomic = { 0 };

    if (selection == NULL || atomic_buffer == NULL ||
        selection->crtc_id == 0 || current_fb_id == 0) {
        errno = EINVAL;
        return vc4_master_reacquire_fail("invalid-atomic-swap-request");
    }
    if (vc4_master_reacquire_primary_plane(
            fd, selection->crtc_id, current_fb_id, &primary) < 0) {
        return -1;
    }
    if (vc4_master_reacquire_object_property(
            fd, selection->crtc_id, DRM_MODE_OBJECT_CRTC, "ACTIVE",
            &active_property, &active_value) < 0) {
        return -1;
    }
    if (active_value != 1) {
        errno = EPROTO;
        return vc4_master_reacquire_fail("atomic-crtc-not-active");
    }
    report("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CRTC "
           "crtc=%u active_prop=%u active=%llu\n",
           selection->crtc_id, active_property,
           (unsigned long long)active_value);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CRTC_ACTIVE_OK\n");

    if (vc4_master_reacquire_create_buffer(
            fd, selection->mode.hdisplay, selection->mode.vdisplay,
            VC4_MASTER_REACQUIRE_ATOMIC_BUFFER, atomic_buffer) < 0) {
        return -1;
    }

    object_ids[0] = primary.plane_id;
    object_ids[1] = selection->crtc_id;
    property_ids[0] = primary.fb_id_property;
    property_ids[1] = active_property;
    property_values[0] = atomic_buffer->framebuffer.fb_id;
    property_values[1] = active_value;
    atomic.count_objs = 2;
    atomic.objs_ptr = (uintptr_t)object_ids;
    atomic.count_props_ptr = (uintptr_t)property_counts;
    atomic.props_ptr = (uintptr_t)property_ids;
    atomic.prop_values_ptr = (uintptr_t)property_values;
    atomic.flags = DRM_MODE_ATOMIC_TEST_ONLY;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_ATOMIC, &atomic,
            "atomic-primary-test-only") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_TEST_ONLY_OK\n");

    atomic.flags = DRM_MODE_ATOMIC_NONBLOCK | DRM_MODE_PAGE_FLIP_EVENT;
    atomic.user_data = VC4_MASTER_REACQUIRE_ATOMIC_USER_DATA;
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_IOCTL_START\n");
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_ATOMIC, &atomic,
            "atomic-primary-commit") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_QUEUED\n");

    if (vc4_master_reacquire_wait_event(
            fd, selection->crtc_id,
            VC4_MASTER_REACQUIRE_ATOMIC_USER_DATA) < 0) {
        return -1;
    }

    current.crtc_id = selection->crtc_id;
    if (vc4_master_reacquire_ioctl(
            fd, DRM_IOCTL_MODE_GETCRTC, &current,
            "getcrtc-after-atomic-primary") < 0) {
        return -1;
    }
    if (current.fb_id != atomic_buffer->framebuffer.fb_id) {
        report("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_FB_MISMATCH "
               "expected=%u actual=%u\n",
               atomic_buffer->framebuffer.fb_id, current.fb_id);
        errno = EIO;
        return vc4_master_reacquire_fail(
            "atomic-primary-current-fb-mismatch");
    }
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CURRENT_FB_OK\n");
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_VISUAL_READY\n");
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY\n");
    sleep(VC4_MASTER_REACQUIRE_VISUAL_HOLD_SECONDS);
    marker("VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_OK\n");
    return 0;
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
    struct vc4_master_reacquire_buffer atomic_buffer = {
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
    if (vc4_master_reacquire_unmap_buffer(
            &pageflip_buffer, "munmap-pageflip-dumb") < 0) {
        goto mastered_fail;
    }

    if (vc4_master_reacquire_atomic_swap(
            fd, &selection, current.fb_id, &atomic_buffer) < 0) {
        goto mastered_fail;
    }
    if (vc4_master_reacquire_unmap_buffer(
            &atomic_buffer, "munmap-atomic-dumb") < 0) {
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
        if (atomic_buffer.mapping != MAP_FAILED) {
            (void)munmap(atomic_buffer.mapping, atomic_buffer.create.size);
            atomic_buffer.mapping = MAP_FAILED;
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
