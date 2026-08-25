/*
 * Supervised native-KMS scanout and page-flip witness for VC4.
 *
 * The child owns the DRM master and keeps the final framebuffer alive until
 * QEMU terminates.  The parent receives a pipe notification once SETCRTC and
 * a vblank-driven page-flip event have both completed, so the rest of the PID
 * 1 witness can continue without sacrificing a stable capture surface.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include <drm.h>
#include <drm_mode.h>
#include <poll.h>
#include <signal.h>
#include <sys/wait.h>

#ifndef DRM_MODE_CONNECTOR_WRITEBACK
#define DRM_MODE_CONNECTOR_WRITEBACK 18
#endif

#ifndef DRM_MODE_CONNECTED
#define DRM_MODE_CONNECTED 1
#endif

#ifndef DRM_MODE_TYPE_PREFERRED
#define DRM_MODE_TYPE_PREFERRED (1U << 3)
#endif

#ifndef DRM_MODE_PAGE_FLIP_EVENT
#define DRM_MODE_PAGE_FLIP_EVENT 0x01
#endif

#ifndef DRM_EVENT_FLIP_COMPLETE
#define DRM_EVENT_FLIP_COMPLETE 0x02
#endif

#define VC4_SCANOUT_FORMAT_XRGB8888 UINT32_C(0x34325258)
#define VC4_SCANOUT_TIMEOUT_MS      30000
#define VC4_PAGE_FLIP_TIMEOUT_MS    8000
#define VC4_SCANOUT_PIPE_SUCCESS    'S'

struct vc4_scanout_selection {
    uint32_t connector_id;
    uint32_t crtc_id;
    struct drm_mode_modeinfo mode;
};

struct vc4_scanout_buffer {
    struct drm_mode_create_dumb create;
    struct drm_mode_map_dumb map;
    struct drm_mode_fb_cmd2 framebuffer;
    void *mapping;
};

static void vc4_scanout_report(const char *format, ...)
{
    char buffer[768];
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (length <= 0) {
        return;
    }
    if ((size_t)length >= sizeof(buffer)) {
        length = (int)sizeof(buffer) - 1;
    }
    buffer[length] = '\0';
    marker(buffer);
}

static int vc4_scanout_ioctl(int fd, unsigned long request, void *argument,
                              const char *stage)
{
    int result;

    do {
        result = ioctl(fd, request, argument);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=%s errno=%d (%s)\n",
            stage, errno, strerror(errno));
        return -1;
    }
    return 0;
}

static uint32_t vc4_scanout_select_possible_crtc(
    const struct drm_mode_card_res *resources, const uint32_t *crtcs,
    uint32_t possible_crtcs)
{
    for (uint32_t index = 0; index < resources->count_crtcs; index++) {
        if (possible_crtcs & (UINT32_C(1) << index)) {
            return crtcs[index];
        }
    }
    return 0;
}

static const struct drm_mode_modeinfo *vc4_scanout_select_mode(
    const struct drm_mode_modeinfo *modes, uint32_t count)
{
    for (uint32_t index = 0; index < count; index++) {
        if (modes[index].type & DRM_MODE_TYPE_PREFERRED) {
            return &modes[index];
        }
    }
    return count != 0 ? &modes[0] : NULL;
}

static int vc4_scanout_try_connector(
    int fd, const struct drm_mode_card_res *resources, const uint32_t *crtcs,
    uint32_t connector_id, struct vc4_scanout_selection *selection)
{
    struct drm_mode_get_connector connector = {
        .connector_id = connector_id,
    };
    struct drm_mode_get_encoder encoder = { 0 };
    struct drm_mode_modeinfo *modes = NULL;
    uint32_t *encoders = NULL;
    uint32_t *properties = NULL;
    uint64_t *property_values = NULL;
    const struct drm_mode_modeinfo *mode;
    uint32_t encoder_id;
    uint32_t crtc_id;
    int result = -1;

    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector,
                          "get-connector-counts") < 0) {
        return -1;
    }
    if (connector.connector_type == DRM_MODE_CONNECTOR_WRITEBACK ||
        connector.connection != DRM_MODE_CONNECTED ||
        connector.count_modes == 0) {
        return 1;
    }

    modes = calloc(connector.count_modes, sizeof(*modes));
    encoders = calloc(connector.count_encoders, sizeof(*encoders));
    properties = calloc(connector.count_props, sizeof(*properties));
    property_values = calloc(connector.count_props, sizeof(*property_values));
    if (modes == NULL ||
        (connector.count_encoders != 0 && encoders == NULL) ||
        (connector.count_props != 0 &&
         (properties == NULL || property_values == NULL))) {
        errno = ENOMEM;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=connector-allocate "
            "errno=%d (%s)\n", errno, strerror(errno));
        goto out;
    }

    connector.modes_ptr = (uintptr_t)modes;
    connector.encoders_ptr = (uintptr_t)encoders;
    connector.props_ptr = (uintptr_t)properties;
    connector.prop_values_ptr = (uintptr_t)property_values;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector,
                          "get-connector") < 0) {
        goto out;
    }

    mode = vc4_scanout_select_mode(modes, connector.count_modes);
    if (mode == NULL) {
        errno = ENODEV;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=select-mode errno=%d (%s)\n",
            errno, strerror(errno));
        goto out;
    }

    encoder_id = connector.encoder_id;
    if (encoder_id == 0 && connector.count_encoders != 0) {
        encoder_id = encoders[0];
    }
    if (encoder_id == 0) {
        errno = ENODEV;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=select-encoder "
            "errno=%d (%s)\n", errno, strerror(errno));
        goto out;
    }

    encoder.encoder_id = encoder_id;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_GETENCODER, &encoder,
                          "get-encoder") < 0) {
        goto out;
    }
    crtc_id = encoder.crtc_id;
    if (crtc_id == 0) {
        crtc_id = vc4_scanout_select_possible_crtc(
            resources, crtcs, encoder.possible_crtcs);
    }
    if (crtc_id == 0) {
        errno = ENODEV;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=select-crtc errno=%d (%s)\n",
            errno, strerror(errno));
        goto out;
    }

    selection->connector_id = connector.connector_id;
    selection->crtc_id = crtc_id;
    selection->mode = *mode;
    vc4_scanout_report(
        "VC4_LINUX_KMS_SCANOUT_CONNECTOR_OK connector=%u crtc=%u "
        "encoder=%u mode=%.32s size=%ux%u clock=%u refresh=%u\n",
        selection->connector_id, selection->crtc_id, encoder_id,
        selection->mode.name, selection->mode.hdisplay,
        selection->mode.vdisplay, selection->mode.clock,
        selection->mode.vrefresh);
    result = 0;

out:
    free(modes);
    free(encoders);
    free(properties);
    free(property_values);
    return result;
}

static int vc4_scanout_select(int fd, struct vc4_scanout_selection *selection)
{
    struct drm_mode_card_res resources = { 0 };
    uint32_t *crtcs = NULL;
    uint32_t *connectors = NULL;
    int result = -1;

    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &resources,
                          "get-resources-counts") < 0) {
        return -1;
    }
    if (resources.count_crtcs == 0 || resources.count_connectors == 0) {
        errno = ENODEV;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=empty-resources "
            "crtcs=%u connectors=%u errno=%d (%s)\n",
            resources.count_crtcs, resources.count_connectors,
            errno, strerror(errno));
        return -1;
    }

    crtcs = calloc(resources.count_crtcs, sizeof(*crtcs));
    connectors = calloc(resources.count_connectors, sizeof(*connectors));
    if (crtcs == NULL || connectors == NULL) {
        errno = ENOMEM;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=resource-allocate "
            "errno=%d (%s)\n", errno, strerror(errno));
        goto out;
    }

    resources.crtc_id_ptr = (uintptr_t)crtcs;
    resources.connector_id_ptr = (uintptr_t)connectors;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &resources,
                          "get-resources") < 0) {
        goto out;
    }
    vc4_scanout_report(
        "VC4_LINUX_KMS_SCANOUT_RESOURCES crtcs=%u connectors=%u "
        "encoders=%u fbs=%u min=%ux%u max=%ux%u\n",
        resources.count_crtcs, resources.count_connectors,
        resources.count_encoders, resources.count_fbs,
        resources.min_width, resources.min_height,
        resources.max_width, resources.max_height);

    for (uint32_t index = 0; index < resources.count_connectors; index++) {
        int connector_result = vc4_scanout_try_connector(
            fd, &resources, crtcs, connectors[index], selection);

        if (connector_result == 0) {
            result = 0;
            goto out;
        }
        if (connector_result < 0) {
            goto out;
        }
    }

    errno = ENODEV;
    vc4_scanout_report(
        "VC4_LINUX_KMS_SCANOUT_FAILED stage=no-connected-connector "
        "errno=%d (%s)\n", errno, strerror(errno));

out:
    free(crtcs);
    free(connectors);
    return result;
}

static uint32_t vc4_scanout_pattern_pixel(uint32_t x, uint32_t y,
                                          uint32_t width, uint32_t height,
                                          unsigned pattern)
{
    const uint32_t corner = 48;

    if (x < corner && y < corner) {
        return pattern == 0 ? UINT32_C(0x00ffff00) : UINT32_C(0x00ff0000);
    }
    if (x + corner >= width && y < corner) {
        return pattern == 0 ? UINT32_C(0x0000ffff) : UINT32_C(0x0000ff00);
    }
    if (x < corner && y + corner >= height) {
        return pattern == 0 ? UINT32_C(0x00ff00ff) : UINT32_C(0x000000ff);
    }
    if (x + corner >= width && y + corner >= height) {
        return UINT32_C(0x00ffffff);
    }

    if (pattern == 0) {
        uint32_t red = width > 1 ? x * 255 / (width - 1) : 0;
        uint32_t green = height > 1 ? y * 255 / (height - 1) : 0;
        uint32_t blue = ((x / 32) ^ (y / 32)) & 1 ? 0xf0 : 0x20;

        return (red << 16) | (green << 8) | blue;
    }

    if (((x / 24) + (y / 24)) & 1) {
        return UINT32_C(0x00f020a0);
    }
    return UINT32_C(0x0020d0f0);
}

static void vc4_scanout_fill(struct vc4_scanout_buffer *buffer,
                             unsigned pattern)
{
    for (uint32_t y = 0; y < buffer->create.height; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)buffer->mapping +
                                     (size_t)y * buffer->create.pitch);

        for (uint32_t x = 0; x < buffer->create.width; x++) {
            row[x] = vc4_scanout_pattern_pixel(
                x, y, buffer->create.width, buffer->create.height, pattern);
        }
    }
    __sync_synchronize();
    (void)msync(buffer->mapping, buffer->create.size, MS_SYNC);
}

static int vc4_scanout_create_buffer(int fd, uint32_t width, uint32_t height,
                                     unsigned pattern,
                                     struct vc4_scanout_buffer *buffer)
{
    memset(buffer, 0, sizeof(*buffer));
    buffer->mapping = MAP_FAILED;
    buffer->create.width = width;
    buffer->create.height = height;
    buffer->create.bpp = 32;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &buffer->create,
                          "create-dumb") < 0) {
        return -1;
    }

    buffer->map.handle = buffer->create.handle;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &buffer->map,
                          "map-dumb") < 0) {
        return -1;
    }
    buffer->mapping = mmap(NULL, buffer->create.size,
                           PROT_READ | PROT_WRITE, MAP_SHARED,
                           fd, (off_t)buffer->map.offset);
    if (buffer->mapping == MAP_FAILED) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=mmap-dumb errno=%d (%s)\n",
            errno, strerror(errno));
        return -1;
    }
    vc4_scanout_fill(buffer, pattern);

    buffer->framebuffer.width = width;
    buffer->framebuffer.height = height;
    buffer->framebuffer.pixel_format = VC4_SCANOUT_FORMAT_XRGB8888;
    buffer->framebuffer.handles[0] = buffer->create.handle;
    buffer->framebuffer.pitches[0] = buffer->create.pitch;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_ADDFB2, &buffer->framebuffer,
                          "addfb2") < 0) {
        return -1;
    }
    vc4_scanout_report(
        "VC4_LINUX_KMS_SCANOUT_BUFFER_OK pattern=%u handle=%u fb=%u "
        "pitch=%u size=%llu map=0x%llx\n",
        pattern, buffer->create.handle, buffer->framebuffer.fb_id,
        buffer->create.pitch, (unsigned long long)buffer->create.size,
        (unsigned long long)buffer->map.offset);
    return 0;
}

static void vc4_scanout_destroy_buffer(int fd,
                                       struct vc4_scanout_buffer *buffer)
{
    if (buffer->framebuffer.fb_id != 0) {
        (void)ioctl(fd, DRM_IOCTL_MODE_RMFB, &buffer->framebuffer.fb_id);
    }
    if (buffer->mapping != MAP_FAILED) {
        (void)munmap(buffer->mapping, buffer->create.size);
        buffer->mapping = MAP_FAILED;
    }
    if (buffer->create.handle != 0) {
        struct drm_mode_destroy_dumb destroy = {
            .handle = buffer->create.handle,
        };

        (void)ioctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
        buffer->create.handle = 0;
    }
}

static int vc4_scanout_wait_flip_event(int fd, uint64_t expected_user_data)
{
    struct pollfd descriptor = {
        .fd = fd,
        .events = POLLIN,
    };
    uint8_t events[1024];
    int poll_result;
    ssize_t length;
    size_t offset = 0;

    do {
        poll_result = poll(&descriptor, 1, VC4_PAGE_FLIP_TIMEOUT_MS);
    } while (poll_result < 0 && errno == EINTR);
    if (poll_result == 0) {
        errno = ETIMEDOUT;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=page-flip-event-timeout "
            "errno=%d (%s)\n", errno, strerror(errno));
        return -1;
    }
    if (poll_result < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=page-flip-poll "
            "errno=%d (%s)\n", errno, strerror(errno));
        return -1;
    }

    do {
        length = read(fd, events, sizeof(events));
    } while (length < 0 && errno == EINTR);
    if (length < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=page-flip-read "
            "errno=%d (%s)\n", errno, strerror(errno));
        return -1;
    }

    while (offset + sizeof(struct drm_event) <= (size_t)length) {
        const struct drm_event *event =
            (const struct drm_event *)(events + offset);

        if (event->length < sizeof(*event) ||
            offset + event->length > (size_t)length) {
            break;
        }
        if (event->type == DRM_EVENT_FLIP_COMPLETE &&
            event->length >= sizeof(struct drm_event_vblank)) {
            const struct drm_event_vblank *flip =
                (const struct drm_event_vblank *)event;

            vc4_scanout_report(
                "VC4_LINUX_KMS_PAGE_FLIP_EVENT_OK user_data=0x%llx "
                "sequence=%u tv=%u.%06u\n",
                (unsigned long long)flip->user_data,
                flip->sequence, flip->tv_sec, flip->tv_usec);
            if (flip->user_data == expected_user_data) {
                return 0;
            }
        }
        offset += event->length;
    }

    errno = ENOMSG;
    vc4_scanout_report(
        "VC4_LINUX_KMS_SCANOUT_FAILED stage=page-flip-event-missing "
        "errno=%d (%s)\n", errno, strerror(errno));
    return -1;
}

static int vc4_scanout_child(int ready_fd)
{
    static const uint64_t page_flip_cookie = UINT64_C(0x5643345041474546);
    struct vc4_scanout_selection selection = { 0 };
    struct vc4_scanout_buffer first;
    struct vc4_scanout_buffer second;
    struct drm_mode_crtc crtc = { 0 };
    struct drm_mode_crtc verify = { 0 };
    struct drm_mode_crtc_page_flip flip = { 0 };
    uint32_t connector_id;
    int fd = -1;
    int result = -1;

    marker("VC4_LINUX_KMS_SCANOUT_START\n");
    fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=open-card0 errno=%d (%s)\n",
            errno, strerror(errno));
        goto out;
    }
    if (vc4_scanout_select(fd, &selection) < 0) {
        goto out;
    }
    if (vc4_scanout_create_buffer(fd, selection.mode.hdisplay,
                                  selection.mode.vdisplay, 0, &first) < 0) {
        goto out;
    }
    if (vc4_scanout_create_buffer(fd, selection.mode.hdisplay,
                                  selection.mode.vdisplay, 1, &second) < 0) {
        goto destroy_first;
    }

    connector_id = selection.connector_id;
    crtc.crtc_id = selection.crtc_id;
    crtc.fb_id = first.framebuffer.fb_id;
    crtc.set_connectors_ptr = (uintptr_t)&connector_id;
    crtc.count_connectors = 1;
    crtc.mode = selection.mode;
    crtc.mode_valid = 1;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc,
                          "setcrtc") < 0) {
        goto destroy_second;
    }
    marker("VC4_LINUX_KMS_SETCRTC_OK\n");

    flip.crtc_id = selection.crtc_id;
    flip.fb_id = second.framebuffer.fb_id;
    flip.flags = DRM_MODE_PAGE_FLIP_EVENT;
    flip.user_data = page_flip_cookie;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip,
                          "page-flip") < 0) {
        goto destroy_second;
    }
    marker("VC4_LINUX_KMS_PAGE_FLIP_IOCTL_OK\n");
    if (vc4_scanout_wait_flip_event(fd, page_flip_cookie) < 0) {
        goto destroy_second;
    }
    marker("VC4_LINUX_KMS_PAGE_FLIP_OK\n");

    verify.crtc_id = selection.crtc_id;
    if (vc4_scanout_ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &verify,
                          "getcrtc-after-flip") < 0) {
        goto destroy_second;
    }
    vc4_scanout_report(
        "VC4_LINUX_KMS_SCANOUT_CRTC_OK crtc=%u fb=%u x=%u y=%u mode_valid=%u\n",
        verify.crtc_id, verify.fb_id, verify.x, verify.y, verify.mode_valid);
    if (verify.fb_id != second.framebuffer.fb_id) {
        errno = EIO;
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=verify-flipped-fb "
            "expected=%u actual=%u errno=%d (%s)\n",
            second.framebuffer.fb_id, verify.fb_id,
            errno, strerror(errno));
        goto destroy_second;
    }

    if (write_all(ready_fd, (const char[]){ VC4_SCANOUT_PIPE_SUCCESS }, 1) < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=notify-parent "
            "errno=%d (%s)\n", errno, strerror(errno));
        goto destroy_second;
    }
    marker("VC4_LINUX_KMS_SCANOUT_ARMED\n");
    alarm(0);
    for (;;) {
        pause();
    }

    /* The successful path intentionally keeps both BOs and the DRM fd alive. */

destroy_second:
    vc4_scanout_destroy_buffer(fd, &second);
destroy_first:
    vc4_scanout_destroy_buffer(fd, &first);
out:
    if (fd >= 0) {
        close(fd);
    }
    return result;
}

static int vc4_kms_scanout_supervise(void)
{
    struct pollfd descriptor;
    int pipe_fds[2] = { -1, -1 };
    pid_t child;
    char status = 0;
    int poll_result;
    int child_status;

    marker("VC4_LINUX_KMS_SCANOUT_SUPERVISOR_START\n");
    if (pipe(pipe_fds) < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=pipe errno=%d (%s)\n",
            errno, strerror(errno));
        return -1;
    }

    child = fork();
    if (child < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=fork errno=%d (%s)\n",
            errno, strerror(errno));
        close(pipe_fds[0]);
        close(pipe_fds[1]);
        return -1;
    }
    if (child == 0) {
        close(pipe_fds[0]);
        alarm((VC4_SCANOUT_TIMEOUT_MS / 1000) - 2);
        _exit(vc4_scanout_child(pipe_fds[1]) == 0 ? 0 : 1);
    }

    close(pipe_fds[1]);
    descriptor.fd = pipe_fds[0];
    descriptor.events = POLLIN | POLLHUP;
    do {
        poll_result = poll(&descriptor, 1, VC4_SCANOUT_TIMEOUT_MS);
    } while (poll_result < 0 && errno == EINTR);

    if (poll_result > 0 && (descriptor.revents & POLLIN) != 0 &&
        read(pipe_fds[0], &status, 1) == 1 &&
        status == VC4_SCANOUT_PIPE_SUCCESS) {
        close(pipe_fds[0]);
        marker("VC4_LINUX_KMS_SCANOUT_SUPERVISOR_READY\n");
        return 0;
    }

    close(pipe_fds[0]);
    if (poll_result == 0) {
        (void)kill(child, SIGKILL);
        (void)waitpid(child, NULL, 0);
        marker("VC4_LINUX_KMS_SCANOUT_TIMEOUT\n");
        return -1;
    }
    if (poll_result < 0) {
        vc4_scanout_report(
            "VC4_LINUX_KMS_SCANOUT_FAILED stage=supervisor-poll "
            "errno=%d (%s)\n", errno, strerror(errno));
        (void)kill(child, SIGKILL);
        (void)waitpid(child, NULL, 0);
        return -1;
    }

    if (waitpid(child, &child_status, 0) == child) {
        if (WIFEXITED(child_status)) {
            vc4_scanout_report(
                "VC4_LINUX_KMS_SCANOUT_CHILD_EXIT status=%d\n",
                WEXITSTATUS(child_status));
        } else if (WIFSIGNALED(child_status)) {
            vc4_scanout_report(
                "VC4_LINUX_KMS_SCANOUT_CHILD_SIGNAL signal=%d\n",
                WTERMSIG(child_status));
        }
    }
    marker("VC4_LINUX_KMS_SCANOUT_SUPERVISOR_FAILED\n");
    return -1;
}
