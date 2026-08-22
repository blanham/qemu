/*
 * Linux firmware-KMS scanout witness for the emulated Raspberry Pi 3.
 *
 * Reuse the module loader and low-level logging helpers from the VC4 render
 * witness, then exercise the generic DRM modeset UAPI against firmware KMS.
 */
#define main vc4_linux_v3d_modular_embedded_main
#include "linux-v3d-modular-init.c"
#undef main

#include <drm.h>
#include <drm_fourcc.h>
#include <drm_mode.h>
#include <stdlib.h>

#define FKMS_MAX_OBJECTS 64
#define FKMS_COUNT_OR_ONE(count) ((count) != 0 ? (count) : 1u)

typedef struct FKMSResources {
    struct drm_mode_card_res res;
    uint32_t *crtcs;
    uint32_t *connectors;
    uint32_t *encoders;
    uint32_t *fbs;
} FKMSResources;

typedef struct FKMSConnector {
    uint32_t connector_id;
    uint32_t crtc_id;
    struct drm_mode_modeinfo mode;
} FKMSConnector;

static int fkms_ioctl(int fd, unsigned long request, void *argument)
{
    int result;

    do {
        result = ioctl(fd, request, argument);
    } while (result < 0 && errno == EINTR);
    return result;
}

static void fkms_free_resources(FKMSResources *resources)
{
    free(resources->crtcs);
    free(resources->connectors);
    free(resources->encoders);
    free(resources->fbs);
    memset(resources, 0, sizeof(*resources));
}

static int fkms_get_resources(int fd, FKMSResources *resources)
{
    memset(resources, 0, sizeof(*resources));
    if (fkms_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &resources->res) < 0) {
        report("VC4_LINUX_FKMS_GETRESOURCES_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if (resources->res.count_crtcs == 0 ||
        resources->res.count_connectors == 0 ||
        resources->res.count_crtcs > FKMS_MAX_OBJECTS ||
        resources->res.count_connectors > FKMS_MAX_OBJECTS ||
        resources->res.count_encoders > FKMS_MAX_OBJECTS ||
        resources->res.count_fbs > FKMS_MAX_OBJECTS) {
        report("VC4_LINUX_FKMS_RESOURCES_INVALID crtcs=%u connectors=%u encoders=%u fbs=%u\n",
               resources->res.count_crtcs,
               resources->res.count_connectors,
               resources->res.count_encoders,
               resources->res.count_fbs);
        errno = ENODEV;
        return -1;
    }

    resources->crtcs = calloc(resources->res.count_crtcs,
                              sizeof(*resources->crtcs));
    resources->connectors = calloc(resources->res.count_connectors,
                                   sizeof(*resources->connectors));
    resources->encoders = calloc(FKMS_COUNT_OR_ONE(resources->res.count_encoders),
                                 sizeof(*resources->encoders));
    resources->fbs = calloc(FKMS_COUNT_OR_ONE(resources->res.count_fbs),
                            sizeof(*resources->fbs));
    if (!resources->crtcs || !resources->connectors ||
        !resources->encoders || !resources->fbs) {
        fkms_free_resources(resources);
        errno = ENOMEM;
        return -1;
    }

    resources->res.crtc_id_ptr = (uintptr_t)resources->crtcs;
    resources->res.connector_id_ptr = (uintptr_t)resources->connectors;
    resources->res.encoder_id_ptr = (uintptr_t)resources->encoders;
    resources->res.fb_id_ptr = (uintptr_t)resources->fbs;
    if (fkms_ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &resources->res) < 0) {
        report("VC4_LINUX_FKMS_GETRESOURCES_FILL_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        fkms_free_resources(resources);
        return -1;
    }

    report("VC4_LINUX_FKMS_RESOURCES_OK crtcs=%u connectors=%u encoders=%u min=%ux%u max=%ux%u\n",
           resources->res.count_crtcs,
           resources->res.count_connectors,
           resources->res.count_encoders,
           resources->res.min_width, resources->res.min_height,
           resources->res.max_width, resources->res.max_height);
    marker("VC4_LINUX_FKMS_RESOURCES_OK\n");
    return 0;
}

static uint32_t fkms_pick_crtc(int fd, const FKMSResources *resources,
                               const struct drm_mode_get_connector *connector,
                               const uint32_t *encoder_ids)
{
    uint32_t preferred = connector->encoder_id;

    for (uint32_t pass = 0; pass < 2; pass++) {
        uint32_t count = pass == 0 && preferred != 0 ? 1 :
                         connector->count_encoders;

        for (uint32_t index = 0; index < count; index++) {
            struct drm_mode_get_encoder encoder = { 0 };

            encoder.encoder_id = pass == 0 && preferred != 0 ?
                                 preferred : encoder_ids[index];
            if (encoder.encoder_id == 0 ||
                fkms_ioctl(fd, DRM_IOCTL_MODE_GETENCODER, &encoder) < 0) {
                continue;
            }
            if (encoder.crtc_id != 0) {
                return encoder.crtc_id;
            }
            for (uint32_t crtc = 0; crtc < resources->res.count_crtcs;
                 crtc++) {
                if (encoder.possible_crtcs & (UINT32_C(1) << crtc)) {
                    return resources->crtcs[crtc];
                }
            }
        }
        if (preferred == 0) {
            break;
        }
        preferred = 0;
    }
    return resources->crtcs[0];
}

static int fkms_find_connector(int fd, const FKMSResources *resources,
                               FKMSConnector *selected)
{
    memset(selected, 0, sizeof(*selected));

    for (uint32_t index = 0; index < resources->res.count_connectors; index++) {
        struct drm_mode_get_connector connector = { 0 };
        struct drm_mode_modeinfo *modes = NULL;
        uint32_t *encoders = NULL;
        uint32_t *properties = NULL;
        uint64_t *property_values = NULL;
        uint32_t mode_index = 0;

        connector.connector_id = resources->connectors[index];
        /* A zero-capacity query by the DRM master forces connector probing. */
        if (fkms_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector) < 0) {
            report("VC4_LINUX_FKMS_GETCONNECTOR_FAILED id=%u errno=%d (%s)\n",
                   connector.connector_id, errno, strerror(errno));
            continue;
        }
        if (connector.count_modes > FKMS_MAX_OBJECTS ||
            connector.count_encoders > FKMS_MAX_OBJECTS ||
            connector.count_props > FKMS_MAX_OBJECTS) {
            continue;
        }

        modes = calloc(FKMS_COUNT_OR_ONE(connector.count_modes), sizeof(*modes));
        encoders = calloc(FKMS_COUNT_OR_ONE(connector.count_encoders),
                          sizeof(*encoders));
        properties = calloc(FKMS_COUNT_OR_ONE(connector.count_props),
                            sizeof(*properties));
        property_values = calloc(FKMS_COUNT_OR_ONE(connector.count_props),
                                 sizeof(*property_values));
        if (!modes || !encoders || !properties || !property_values) {
            free(modes);
            free(encoders);
            free(properties);
            free(property_values);
            errno = ENOMEM;
            return -1;
        }

        connector.modes_ptr = (uintptr_t)modes;
        connector.encoders_ptr = (uintptr_t)encoders;
        connector.props_ptr = (uintptr_t)properties;
        connector.prop_values_ptr = (uintptr_t)property_values;
        if (fkms_ioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &connector) < 0) {
            report("VC4_LINUX_FKMS_GETCONNECTOR_FILL_FAILED id=%u errno=%d (%s)\n",
                   connector.connector_id, errno, strerror(errno));
            free(modes);
            free(encoders);
            free(properties);
            free(property_values);
            continue;
        }

        report("VC4_LINUX_FKMS_CONNECTOR id=%u type=%u connection=%u modes=%u encoders=%u\n",
               connector.connector_id, connector.connector_type,
               connector.connection, connector.count_modes,
               connector.count_encoders);
        if (connector.connection == DRM_MODE_CONNECTED &&
            connector.count_modes != 0) {
            for (uint32_t mode = 0; mode < connector.count_modes; mode++) {
                if (modes[mode].type & DRM_MODE_TYPE_PREFERRED) {
                    mode_index = mode;
                    break;
                }
            }
            selected->connector_id = connector.connector_id;
            selected->crtc_id = fkms_pick_crtc(fd, resources, &connector,
                                                encoders);
            selected->mode = modes[mode_index];
            free(modes);
            free(encoders);
            free(properties);
            free(property_values);
            report("VC4_LINUX_FKMS_CONNECTOR_OK connector=%u crtc=%u mode=%s %ux%u clock=%u\n",
                   selected->connector_id, selected->crtc_id,
                   selected->mode.name, selected->mode.hdisplay,
                   selected->mode.vdisplay, selected->mode.clock);
            marker("VC4_LINUX_FKMS_CONNECTOR_OK\n");
            return 0;
        }

        free(modes);
        free(encoders);
        free(properties);
        free(property_values);
    }

    errno = ENODEV;
    return -1;
}

static void fkms_paint_quadrants(void *mapping, uint32_t width,
                                 uint32_t height, uint32_t pitch)
{
    static const uint32_t colors[4] = {
        UINT32_C(0x00ff0000),
        UINT32_C(0x0000ff00),
        UINT32_C(0x000000ff),
        UINT32_C(0x00ffffff),
    };

    for (uint32_t y = 0; y < height; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)mapping + (size_t)y * pitch);
        uint32_t vertical = y >= height / 2;

        for (uint32_t x = 0; x < width; x++) {
            row[x] = colors[vertical * 2 + (x >= width / 2)];
        }
    }
}

static int fkms_modeset(int fd, const FKMSConnector *connector)
{
    struct drm_mode_create_dumb create = { 0 };
    struct drm_mode_map_dumb map = { 0 };
    struct drm_mode_fb_cmd2 fb = { 0 };
    struct drm_mode_crtc crtc = { 0 };
    struct timespec settle = { .tv_nsec = 500000000 };
    void *mapping;

    create.width = connector->mode.hdisplay;
    create.height = connector->mode.vdisplay;
    create.bpp = 32;
    if (fkms_ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create) < 0) {
        report("VC4_LINUX_FKMS_CREATE_DUMB_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    report("VC4_LINUX_FKMS_CREATE_DUMB_OK handle=%u pitch=%u size=%llu\n",
           create.handle, create.pitch, (unsigned long long)create.size);
    marker("VC4_LINUX_FKMS_CREATE_DUMB_OK\n");

    map.handle = create.handle;
    if (fkms_ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map) < 0) {
        report("VC4_LINUX_FKMS_MAP_DUMB_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    mapping = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                   fd, map.offset);
    if (mapping == MAP_FAILED) {
        report("VC4_LINUX_FKMS_MMAP_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    fkms_paint_quadrants(mapping, create.width, create.height, create.pitch);
    if (msync(mapping, create.size, MS_SYNC) < 0) {
        report("VC4_LINUX_FKMS_MSYNC_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    marker("VC4_LINUX_FKMS_MMAP_OK\n");

    fb.width = create.width;
    fb.height = create.height;
    fb.pixel_format = DRM_FORMAT_XRGB8888;
    fb.handles[0] = create.handle;
    fb.pitches[0] = create.pitch;
    if (fkms_ioctl(fd, DRM_IOCTL_MODE_ADDFB2, &fb) < 0) {
        struct drm_mode_fb_cmd legacy = { 0 };

        legacy.width = create.width;
        legacy.height = create.height;
        legacy.pitch = create.pitch;
        legacy.bpp = 32;
        legacy.depth = 24;
        legacy.handle = create.handle;
        if (fkms_ioctl(fd, DRM_IOCTL_MODE_ADDFB, &legacy) < 0) {
            report("VC4_LINUX_FKMS_ADDFB_FAILED errno=%d (%s)\n",
                   errno, strerror(errno));
            return -1;
        }
        fb.fb_id = legacy.fb_id;
    }
    report("VC4_LINUX_FKMS_ADDFB_OK fb=%u\n", fb.fb_id);
    marker("VC4_LINUX_FKMS_ADDFB_OK\n");

    crtc.set_connectors_ptr = (uintptr_t)&connector->connector_id;
    crtc.count_connectors = 1;
    crtc.crtc_id = connector->crtc_id;
    crtc.fb_id = fb.fb_id;
    crtc.mode_valid = 1;
    crtc.mode = connector->mode;
    if (fkms_ioctl(fd, DRM_IOCTL_MODE_SETCRTC, &crtc) < 0) {
        report("VC4_LINUX_FKMS_SETCRTC_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    nanosleep(&settle, NULL);
    report("VC4_LINUX_FKMS_MODESET_OK connector=%u crtc=%u fb=%u mode=%ux%u\n",
           connector->connector_id, connector->crtc_id, fb.fb_id,
           connector->mode.hdisplay, connector->mode.vdisplay);
    marker("VC4_LINUX_FKMS_MODESET_OK\n");
    marker("VC4_LINUX_FKMS_SCANOUT_OK\n");
    marker("VC4_LINUX_FB_OK\n");

    /* Keep the dumb buffer, framebuffer, and master fd alive for inspection. */
    return 0;
}

int main(void)
{
    struct timespec settle = { .tv_sec = 2 };
    VC4DRMNode card;
    FKMSResources resources;
    FKMSConnector connector;
    int module_result;
    int modeset_result = -1;

    prepare_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_FKMS_START\n");
    module_result = load_vc4_module_manifest();
    if (module_result == 0) {
        marker("VC4_LINUX_MODULE_LOAD_DONE\n");
        marker("VC4_LINUX_MODULE_CLOSURE_OK\n");
    }
    nanosleep(&settle, NULL);
    report_topology();

    card = open_drm_node("CARD0", "/dev/dri/card0");
    if (module_result == 0 && card.fd >= 0 && card.vc4) {
        marker("VC4_LINUX_FKMS_CARD0_OK\n");
        if (fkms_get_resources(card.fd, &resources) == 0) {
            if (fkms_find_connector(card.fd, &resources, &connector) == 0) {
                modeset_result = fkms_modeset(card.fd, &connector);
            }
            fkms_free_resources(&resources);
        }
    }

    report("VC4_LINUX_FKMS_DONE modules=%d card0=%d modeset=%d\n",
           module_result, card.fd >= 0 && card.vc4 ? 0 : -1,
           modeset_result);
    if (modeset_result == 0) {
        marker("VC4_LINUX_FKMS_OK\n");
    } else {
        marker("VC4_LINUX_FKMS_PARTIAL\n");
    }
    marker("VC4_LINUX_INIT_IDLE\n");
    for (;;) {
        pause();
    }
}
