/*
 * Supervised native-KMS modeset witness for the pinned Linux VC4 fixture.
 *
 * This file is included after linux-v3d-modular-init.c's compact DRM
 * topology UAPI declarations.  The probe deliberately inherits the already
 * open card FD: that file owns DRM master, unlike a separately reopened
 * /dev/dri/card0.  The child is bounded by a parent-side timeout so an
 * emulated commit stall cannot wedge PID 1 or hide the exact frontier.
 */

#include <drm_mode.h>
#include <signal.h>
#include <sys/wait.h>

#ifndef DRM_MODE_CONNECTOR_WRITEBACK
#define DRM_MODE_CONNECTOR_WRITEBACK 18
#endif

#ifndef DRM_MODE_CONNECTED
#define DRM_MODE_CONNECTED 1
#endif

#define VC4_MODESET_DRM_FORMAT_XRGB8888 UINT32_C(0x34325258)
#define VC4_MODESET_TIMEOUT_SECONDS     30U
#define VC4_MODESET_POLL_NS             100000000L

struct vc4_modeset_selection {
    uint32_t connector_id;
    uint32_t crtc_id;
    struct drm_mode_modeinfo mode;
};

static int vc4_modeset_fail(const char *stage)
{
    char message[192];
    int saved_errno = errno != 0 ? errno : EIO;

    snprintf(message, sizeof(message),
             "VC4_LINUX_KMS_MODESET_FAILED stage=%s errno=%d\n",
             stage, saved_errno);
    marker(message);
    errno = saved_errno;
    return -1;
}

static int vc4_modeset_ioctl(int fd, unsigned long request, void *argument,
                             const char *stage)
{
    int result;

    do {
        result = ioctl(fd, request, argument);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        return vc4_modeset_fail(stage);
    }
    return 0;
}

static uint32_t vc4_modeset_possible_crtc(
    const struct drm_mode_card_res *resources, const uint32_t *crtc_ids,
    uint32_t possible_crtcs)
{
    for (uint32_t index = 0; index < resources->count_crtcs; index++) {
        if (possible_crtcs & (UINT32_C(1) << index)) {
            return crtc_ids[index];
        }
    }
    return 0;
}

static int vc4_modeset_select_encoder(
    int fd, const struct drm_mode_card_res *resources,
    const uint32_t *crtc_ids, const uint32_t *encoder_ids,
    uint32_t encoder_count, uint32_t preferred_encoder,
    uint32_t *selected_crtc)
{
    uint32_t candidate_ids[VC4_KMS_MAX_ENCODERS + 1] = { 0 };
    uint32_t candidate_count = 0;
    int saved_errno = ENODEV;

    if (preferred_encoder != 0) {
        candidate_ids[candidate_count++] = preferred_encoder;
    }
    for (uint32_t index = 0;
         index < encoder_count &&
         candidate_count < VC4_KMS_MAX_ENCODERS + 1;
         index++) {
        bool duplicate = false;

        for (uint32_t prior = 0; prior < candidate_count; prior++) {
            if (candidate_ids[prior] == encoder_ids[index]) {
                duplicate = true;
                break;
            }
        }
        if (!duplicate && encoder_ids[index] != 0) {
            candidate_ids[candidate_count++] = encoder_ids[index];
        }
    }

    for (uint32_t index = 0; index < candidate_count; index++) {
        struct drm_mode_get_encoder encoder = {
            .encoder_id = candidate_ids[index],
        };
        uint32_t crtc_id;

        if (ioctl(fd, DRM_IOCTL_MODE_GETENCODER, &encoder) < 0) {
            saved_errno = errno;
            continue;
        }
        crtc_id = encoder.crtc_id;
        if (crtc_id == 0) {
            crtc_id = vc4_modeset_possible_crtc(
                resources, crtc_ids, encoder.possible_crtcs);
        }
        if (crtc_id != 0) {
            *selected_crtc = crtc_id;
            return 0;
        }
    }

    errno = saved_errno;
    return 1;
}

static int vc4_modeset_select(int fd,
                              struct vc4_modeset_selection *selection)
{
    struct drm_mode_card_res resources = { 0 };
    uint32_t fb_ids[VC4_KMS_MAX_FBS] = { 0 };
    uint32_t crtc_ids[VC4_KMS_MAX_CRTCS] = { 0 };
    uint32_t connector_ids[VC4_KMS_MAX_CONNECTORS] = { 0 };
    uint32_t encoder_ids[VC4_KMS_MAX_ENCODERS] = { 0 };

    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &resources,
                          "get-resources-counts") < 0) {
        return -1;
    }
    if (resources.count_fbs > VC4_KMS_MAX_FBS ||
        resources.count_crtcs > VC4_KMS_MAX_CRTCS ||
        resources.count_connectors > VC4_KMS_MAX_CONNECTORS ||
        resources.count_encoders > VC4_KMS_MAX_ENCODERS) {
        errno = EOVERFLOW;
        return vc4_modeset_fail("resource-capacity");
    }
    if (resources.count_crtcs == 0 || resources.count_connectors == 0) {
        errno = ENODEV;
        return vc4_modeset_fail("resources-empty");
    }

    /*
     * GETRESOURCES copies every nonzero-count array.  Supplying only the
     * CRTC and connector pointers produces EFAULT when the card also reports
     * framebuffers or encoders, which was the defect in the first one-shot
     * witness.  Always provide all four buffers.
     */
    resources.fb_id_ptr = (uintptr_t)fb_ids;
    resources.crtc_id_ptr = (uintptr_t)crtc_ids;
    resources.connector_id_ptr = (uintptr_t)connector_ids;
    resources.encoder_id_ptr = (uintptr_t)encoder_ids;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &resources,
                          "get-resources") < 0) {
        return -1;
    }
    if (resources.count_fbs > VC4_KMS_MAX_FBS ||
        resources.count_crtcs > VC4_KMS_MAX_CRTCS ||
        resources.count_connectors > VC4_KMS_MAX_CONNECTORS ||
        resources.count_encoders > VC4_KMS_MAX_ENCODERS) {
        errno = EAGAIN;
        return vc4_modeset_fail("resource-race");
    }
    marker("VC4_LINUX_KMS_MODESET_RESOURCES_OK\n");

    for (uint32_t index = 0; index < resources.count_connectors; index++) {
        struct drm_mode_get_connector connector = {
            .connector_id = connector_ids[index],
        };
        struct drm_mode_modeinfo modes[VC4_KMS_MAX_MODES] = { 0 };
        uint32_t encoders[VC4_KMS_MAX_ENCODERS] = { 0 };
        uint32_t properties[VC4_KMS_MAX_PROPERTIES] = { 0 };
        uint64_t property_values[VC4_KMS_MAX_PROPERTIES] = { 0 };
        uint32_t crtc_id = 0;

        if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector) < 0) {
            continue;
        }
        if (connector.count_modes > VC4_KMS_MAX_MODES ||
            connector.count_encoders > VC4_KMS_MAX_ENCODERS ||
            connector.count_props > VC4_KMS_MAX_PROPERTIES) {
            continue;
        }

        connector.modes_ptr = (uintptr_t)modes;
        connector.encoders_ptr = (uintptr_t)encoders;
        connector.props_ptr = (uintptr_t)properties;
        connector.prop_values_ptr = (uintptr_t)property_values;
        if (ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector) < 0) {
            continue;
        }
        if (connector.count_modes > VC4_KMS_MAX_MODES ||
            connector.count_encoders > VC4_KMS_MAX_ENCODERS ||
            connector.count_props > VC4_KMS_MAX_PROPERTIES ||
            connector.connector_type == DRM_MODE_CONNECTOR_WRITEBACK ||
            connector.connection != DRM_MODE_CONNECTED ||
            connector.count_modes == 0) {
            continue;
        }
        if (vc4_modeset_select_encoder(
                fd, &resources, crtc_ids, encoders,
                connector.count_encoders, connector.encoder_id,
                &crtc_id) != 0) {
            continue;
        }

        selection->connector_id = connector.connector_id;
        selection->crtc_id = crtc_id;
        selection->mode = modes[0];
        report("VC4_LINUX_KMS_MODESET_CONNECTOR_OK connector=%u "
               "crtc=%u mode=%ux%u clock=%u\n",
               selection->connector_id, selection->crtc_id,
               selection->mode.hdisplay, selection->mode.vdisplay,
               selection->mode.clock);
        marker("VC4_LINUX_KMS_MODESET_CONNECTOR_OK\n");
        return 0;
    }

    errno = ENODEV;
    return vc4_modeset_fail("select-connected-connector");
}

static void vc4_modeset_fill_pattern(void *mapping, uint32_t pitch,
                                     uint32_t width, uint32_t height)
{
    for (uint32_t y = 0; y < height; y++) {
        uint32_t *row =
            (uint32_t *)((uint8_t *)mapping + (size_t)y * pitch);

        for (uint32_t x = 0; x < width; x++) {
            uint32_t red = width > 1 ? x * 255 / (width - 1) : 0;
            uint32_t green = height > 1 ? y * 255 / (height - 1) : 0;
            uint32_t blue = ((x / 32) ^ (y / 32)) & 1 ? 0xff : 0x20;

            row[x] = (red << 16) | (green << 8) | blue;
        }
    }
}

static int vc4_modeset_run(int fd)
{
    struct vc4_modeset_selection selection = { 0 };
    struct drm_mode_create_dumb create = { 0 };
    struct drm_mode_map_dumb map = { 0 };
    struct drm_mode_fb_cmd2 framebuffer = { 0 };
    struct drm_mode_crtc crtc = { 0 };
    void *mapping = MAP_FAILED;
    uint32_t connector_id;

    marker("VC4_LINUX_KMS_MODESET_START\n");
    if (fd < 0) {
        errno = EBADF;
        return vc4_modeset_fail("invalid-master-fd");
    }
    if (vc4_modeset_select(fd, &selection) < 0) {
        return -1;
    }

    create.width = selection.mode.hdisplay;
    create.height = selection.mode.vdisplay;
    create.bpp = 32;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create,
                          "create-dumb") < 0) {
        return -1;
    }
    if (create.handle == 0 || create.pitch == 0 || create.size == 0) {
        errno = EIO;
        return vc4_modeset_fail("create-dumb-invalid");
    }
    report("VC4_LINUX_KMS_MODESET_DUMB_OK handle=%u pitch=%u size=%llu\n",
           create.handle, create.pitch, (unsigned long long)create.size);
    marker("VC4_LINUX_KMS_MODESET_DUMB_OK\n");

    map.handle = create.handle;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map,
                          "map-dumb") < 0) {
        return -1;
    }
    mapping = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd, (off_t)map.offset);
    if (mapping == MAP_FAILED) {
        return vc4_modeset_fail("mmap-dumb");
    }
    vc4_modeset_fill_pattern(mapping, create.pitch,
                             create.width, create.height);
    if (msync(mapping, create.size, MS_SYNC) < 0) {
        return vc4_modeset_fail("msync-dumb");
    }
    marker("VC4_LINUX_KMS_MODESET_MAP_OK\n");

    framebuffer.width = create.width;
    framebuffer.height = create.height;
    framebuffer.pixel_format = VC4_MODESET_DRM_FORMAT_XRGB8888;
    framebuffer.handles[0] = create.handle;
    framebuffer.pitches[0] = create.pitch;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_ADDFB2, &framebuffer,
                          "addfb2") < 0) {
        return -1;
    }
    report("VC4_LINUX_KMS_MODESET_FB_OK fb=%u\n", framebuffer.fb_id);
    marker("VC4_LINUX_KMS_MODESET_FB_OK\n");

    connector_id = selection.connector_id;
    crtc.crtc_id = selection.crtc_id;
    crtc.fb_id = framebuffer.fb_id;
    crtc.set_connectors_ptr = (uintptr_t)&connector_id;
    crtc.count_connectors = 1;
    crtc.mode = selection.mode;
    crtc.mode_valid = 1;
    marker("VC4_LINUX_KMS_MODESET_SETCRTC_START\n");
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc,
                          "setcrtc") < 0) {
        return -1;
    }
    marker("VC4_LINUX_KMS_MODESET_SETCRTC_OK\n");

    /*
     * Leave the active FB and dumb BO attached to the inherited drm_file.
     * The parent keeps that file open for the rest of the witness; tearing
     * the resources down from the child would invalidate the very scanout
     * state being tested.
     */
    (void)munmap(mapping, create.size);
    sleep(2);
    marker("VC4_LINUX_KMS_MODESET_OK\n");
    return 0;
}

static int vc4_kms_modeset_supervise(int master_fd)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MODESET_POLL_NS,
    };
    pid_t child;

    marker("VC4_LINUX_KMS_MODESET_SUPERVISOR_START\n");
    child = fork();
    if (child < 0) {
        return vc4_modeset_fail("fork");
    }
    if (child == 0) {
        alarm(VC4_MODESET_TIMEOUT_SECONDS - 2);
        _exit(vc4_modeset_run(master_fd) == 0 ? 0 : 1);
    }

    for (unsigned int iteration = 0;
         iteration < VC4_MODESET_TIMEOUT_SECONDS * 10;
         iteration++) {
        int status;
        pid_t result = waitpid(child, &status, WNOHANG);

        if (result == child) {
            if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
                marker("VC4_LINUX_KMS_MODESET_SUPERVISOR_OK\n");
                return 0;
            }
            if (WIFEXITED(status)) {
                report("VC4_LINUX_KMS_MODESET_CHILD_EXIT status=%d\n",
                       WEXITSTATUS(status));
            } else if (WIFSIGNALED(status)) {
                report("VC4_LINUX_KMS_MODESET_CHILD_SIGNAL signal=%d\n",
                       WTERMSIG(status));
            }
            marker("VC4_LINUX_KMS_MODESET_SUPERVISOR_FAILED\n");
            return -1;
        }
        if (result < 0) {
            return vc4_modeset_fail("waitpid");
        }
        nanosleep(&delay, NULL);
    }

    (void)kill(child, SIGKILL);
    (void)waitpid(child, NULL, 0);
    marker("VC4_LINUX_KMS_MODESET_TIMEOUT\n");
    return -1;
}
