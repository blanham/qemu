/*
 * Supervised native-KMS modeset probe for the pinned Linux VC4 witness.
 *
 * This file is included by linux-v3d-modular-init.c on the dedicated
 * modeset frontier branch.  Keep it self-contained so the topology witness
 * remains easy to compare with the renderer-only and KMS-enumeration gates.
 */

#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef DRM_MODE_CONNECTOR_WRITEBACK
#define DRM_MODE_CONNECTOR_WRITEBACK 18
#endif

#ifndef DRM_MODE_CONNECTED
#define DRM_MODE_CONNECTED 1
#endif

#define VC4_DRM_FORMAT_XRGB8888 UINT32_C(0x34325258)
#define VC4_MODESET_TIMEOUT_SECONDS 30
#define VC4_MODESET_POLL_NS 100000000L

struct vc4_modeset_selection {
    uint32_t connector_id;
    uint32_t crtc_id;
    struct drm_mode_modeinfo mode;
};

static void vc4_modeset_log_errno(const char *stage)
{
    printf("VC4_LINUX_KMS_MODESET_FAILED stage=%s errno=%d\n", stage, errno);
    fflush(stdout);
}

static int vc4_modeset_ioctl(int fd, unsigned long request, void *argument,
                             const char *stage)
{
    int rc;

    do {
        rc = ioctl(fd, request, argument);
    } while (rc < 0 && errno == EINTR);
    if (rc < 0) {
        vc4_modeset_log_errno(stage);
        return -1;
    }
    return 0;
}

static int vc4_modeset_get_resources(int fd, struct drm_mode_card_res *resources,
                                     uint32_t **crtcs, uint32_t **connectors)
{
    memset(resources, 0, sizeof(*resources));
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, resources,
                          "get-resources-counts") < 0) {
        return -1;
    }
    if (!resources->count_crtcs || !resources->count_connectors) {
        errno = ENODEV;
        vc4_modeset_log_errno("get-resources-empty");
        return -1;
    }

    *crtcs = calloc(resources->count_crtcs, sizeof(**crtcs));
    *connectors = calloc(resources->count_connectors, sizeof(**connectors));
    if (!*crtcs || !*connectors) {
        errno = ENOMEM;
        vc4_modeset_log_errno("get-resources-allocate");
        free(*crtcs);
        free(*connectors);
        *crtcs = NULL;
        *connectors = NULL;
        return -1;
    }

    resources->crtc_id_ptr = (uintptr_t)*crtcs;
    resources->connector_id_ptr = (uintptr_t)*connectors;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, resources,
                          "get-resources") < 0) {
        free(*crtcs);
        free(*connectors);
        *crtcs = NULL;
        *connectors = NULL;
        return -1;
    }
    return 0;
}

static uint32_t vc4_modeset_select_possible_crtc(
    const struct drm_mode_card_res *resources, const uint32_t *crtcs,
    uint32_t possible_crtcs)
{
    uint32_t index;

    for (index = 0; index < resources->count_crtcs; index++) {
        if (possible_crtcs & (UINT32_C(1) << index)) {
            return crtcs[index];
        }
    }
    return 0;
}

static int vc4_modeset_try_connector(
    int fd, const struct drm_mode_card_res *resources, const uint32_t *crtcs,
    uint32_t connector_id, struct vc4_modeset_selection *selection)
{
    struct drm_mode_get_connector connector;
    struct drm_mode_get_encoder encoder;
    struct drm_mode_modeinfo *modes = NULL;
    uint32_t *encoders = NULL;
    uint32_t *props = NULL;
    uint64_t *prop_values = NULL;
    uint32_t encoder_id;
    uint32_t crtc_id = 0;
    int result = -1;

    memset(&connector, 0, sizeof(connector));
    connector.connector_id = connector_id;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector,
                          "get-connector-counts") < 0) {
        return -1;
    }
    if (connector.connector_type == DRM_MODE_CONNECTOR_WRITEBACK ||
        connector.connection != DRM_MODE_CONNECTED || !connector.count_modes) {
        return 1;
    }

    modes = calloc(connector.count_modes, sizeof(*modes));
    encoders = calloc(connector.count_encoders, sizeof(*encoders));
    props = calloc(connector.count_props, sizeof(*props));
    prop_values = calloc(connector.count_props, sizeof(*prop_values));
    if (!modes || (connector.count_encoders && !encoders) ||
        (connector.count_props && (!props || !prop_values))) {
        errno = ENOMEM;
        vc4_modeset_log_errno("get-connector-allocate");
        goto done;
    }

    connector.modes_ptr = (uintptr_t)modes;
    connector.encoders_ptr = (uintptr_t)encoders;
    connector.props_ptr = (uintptr_t)props;
    connector.prop_values_ptr = (uintptr_t)prop_values;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector,
                          "get-connector") < 0) {
        goto done;
    }

    encoder_id = connector.encoder_id;
    if (!encoder_id && connector.count_encoders) {
        encoder_id = encoders[0];
    }
    if (!encoder_id) {
        errno = ENODEV;
        vc4_modeset_log_errno("select-encoder");
        goto done;
    }

    memset(&encoder, 0, sizeof(encoder));
    encoder.encoder_id = encoder_id;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_GETENCODER, &encoder,
                          "get-encoder") < 0) {
        goto done;
    }
    crtc_id = encoder.crtc_id;
    if (!crtc_id) {
        crtc_id = vc4_modeset_select_possible_crtc(resources, crtcs,
                                                    encoder.possible_crtcs);
    }
    if (!crtc_id) {
        errno = ENODEV;
        vc4_modeset_log_errno("select-crtc");
        goto done;
    }

    selection->connector_id = connector.connector_id;
    selection->crtc_id = crtc_id;
    selection->mode = modes[0];
    printf("VC4_LINUX_KMS_MODESET_CONNECTOR_OK connector=%u crtc=%u "
           "mode=%ux%u clock=%u\n",
           selection->connector_id, selection->crtc_id,
           selection->mode.hdisplay, selection->mode.vdisplay,
           selection->mode.clock);
    fflush(stdout);
    result = 0;

done:
    free(modes);
    free(encoders);
    free(props);
    free(prop_values);
    return result;
}

static int vc4_modeset_select(int fd, struct vc4_modeset_selection *selection)
{
    struct drm_mode_card_res resources;
    uint32_t *crtcs = NULL;
    uint32_t *connectors = NULL;
    uint32_t index;
    int rc = -1;

    if (vc4_modeset_get_resources(fd, &resources, &crtcs, &connectors) < 0) {
        return -1;
    }
    for (index = 0; index < resources.count_connectors; index++) {
        int result = vc4_modeset_try_connector(fd, &resources, crtcs,
                                                connectors[index], selection);
        if (result == 0) {
            rc = 0;
            break;
        }
        if (result < 0) {
            rc = -1;
            break;
        }
    }
    if (rc < 0 && !errno) {
        errno = ENODEV;
        vc4_modeset_log_errno("select-connected-connector");
    }
    free(crtcs);
    free(connectors);
    return rc;
}

static void vc4_modeset_fill_pattern(void *mapping, uint32_t pitch,
                                     uint32_t width, uint32_t height)
{
    uint32_t y;

    for (y = 0; y < height; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)mapping + (size_t)y * pitch);
        uint32_t x;

        for (x = 0; x < width; x++) {
            uint32_t red = width > 1 ? x * 255 / (width - 1) : 0;
            uint32_t green = height > 1 ? y * 255 / (height - 1) : 0;
            uint32_t blue = ((x / 32) ^ (y / 32)) & 1 ? 0xff : 0x20;

            row[x] = (red << 16) | (green << 8) | blue;
        }
    }
}

static int vc4_modeset_run(void)
{
    struct vc4_modeset_selection selection;
    struct drm_mode_create_dumb create;
    struct drm_mode_map_dumb map;
    struct drm_mode_fb_cmd2 framebuffer;
    struct drm_mode_crtc crtc;
    struct drm_mode_destroy_dumb destroy;
    void *mapping = MAP_FAILED;
    uint32_t connector_id;
    int fd = -1;
    int result = 1;

    printf("VC4_LINUX_KMS_MODESET_START\n");
    fflush(stdout);
    fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        vc4_modeset_log_errno("open-card0");
        goto done;
    }
    memset(&selection, 0, sizeof(selection));
    if (vc4_modeset_select(fd, &selection) < 0) {
        goto done;
    }

    memset(&create, 0, sizeof(create));
    create.width = selection.mode.hdisplay;
    create.height = selection.mode.vdisplay;
    create.bpp = 32;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create,
                          "create-dumb") < 0) {
        goto done;
    }
    printf("VC4_LINUX_KMS_MODESET_DUMB_OK handle=%u pitch=%u size=%llu\n",
           create.handle, create.pitch, (unsigned long long)create.size);
    fflush(stdout);

    memset(&map, 0, sizeof(map));
    map.handle = create.handle;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map,
                          "map-dumb") < 0) {
        goto destroy_dumb;
    }
    mapping = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd, map.offset);
    if (mapping == MAP_FAILED) {
        vc4_modeset_log_errno("mmap-dumb");
        goto destroy_dumb;
    }
    vc4_modeset_fill_pattern(mapping, create.pitch, create.width, create.height);
    if (msync(mapping, create.size, MS_SYNC) < 0) {
        vc4_modeset_log_errno("msync-dumb");
        goto unmap_dumb;
    }
    printf("VC4_LINUX_KMS_MODESET_MAP_OK\n");
    fflush(stdout);

    memset(&framebuffer, 0, sizeof(framebuffer));
    framebuffer.width = create.width;
    framebuffer.height = create.height;
    framebuffer.pixel_format = VC4_DRM_FORMAT_XRGB8888;
    framebuffer.handles[0] = create.handle;
    framebuffer.pitches[0] = create.pitch;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_ADDFB2, &framebuffer,
                          "addfb2") < 0) {
        goto unmap_dumb;
    }
    printf("VC4_LINUX_KMS_MODESET_FB_OK fb=%u\n", framebuffer.fb_id);
    fflush(stdout);

    connector_id = selection.connector_id;
    memset(&crtc, 0, sizeof(crtc));
    crtc.crtc_id = selection.crtc_id;
    crtc.fb_id = framebuffer.fb_id;
    crtc.set_connectors_ptr = (uintptr_t)&connector_id;
    crtc.count_connectors = 1;
    crtc.mode = selection.mode;
    crtc.mode_valid = 1;
    if (vc4_modeset_ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc,
                          "setcrtc") < 0) {
        goto remove_fb;
    }
    printf("VC4_LINUX_KMS_MODESET_SETCRTC_OK\n");
    fflush(stdout);
    sleep(2);
    printf("VC4_LINUX_KMS_MODESET_OK\n");
    fflush(stdout);
    result = 0;

remove_fb:
    if (ioctl(fd, DRM_IOCTL_MODE_RMFB, &framebuffer.fb_id) < 0 && !result) {
        vc4_modeset_log_errno("rmfb");
        result = 1;
    }
unmap_dumb:
    if (mapping != MAP_FAILED) {
        munmap(mapping, create.size);
    }
destroy_dumb:
    memset(&destroy, 0, sizeof(destroy));
    destroy.handle = create.handle;
    if (create.handle) {
        ioctl(fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
    }
done:
    if (fd >= 0) {
        close(fd);
    }
    return result;
}

static void vc4_kms_modeset_supervise(void)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = VC4_MODESET_POLL_NS,
    };
    pid_t child;
    unsigned int iteration;

    printf("VC4_LINUX_KMS_MODESET_SUPERVISOR_START\n");
    fflush(stdout);
    child = fork();
    if (child < 0) {
        vc4_modeset_log_errno("fork");
        return;
    }
    if (child == 0) {
        alarm(VC4_MODESET_TIMEOUT_SECONDS - 2);
        _exit(vc4_modeset_run());
    }

    for (iteration = 0;
         iteration < VC4_MODESET_TIMEOUT_SECONDS * 10;
         iteration++) {
        int status;
        pid_t result = waitpid(child, &status, WNOHANG);

        if (result == child) {
            if (WIFEXITED(status)) {
                printf("VC4_LINUX_KMS_MODESET_CHILD_EXIT status=%d\n",
                       WEXITSTATUS(status));
            } else if (WIFSIGNALED(status)) {
                printf("VC4_LINUX_KMS_MODESET_CHILD_SIGNAL signal=%d\n",
                       WTERMSIG(status));
            }
            printf("VC4_LINUX_KMS_MODESET_SUPERVISOR_DONE\n");
            fflush(stdout);
            return;
        }
        if (result < 0) {
            vc4_modeset_log_errno("waitpid");
            return;
        }
        nanosleep(&delay, NULL);
    }

    kill(child, SIGKILL);
    waitpid(child, NULL, 0);
    printf("VC4_LINUX_KMS_MODESET_TIMEOUT\n");
    printf("VC4_LINUX_KMS_MODESET_SUPERVISOR_DONE\n");
    fflush(stdout);
}
