/*
 * Linux VC4 module-aware submit witness.
 *
 * Load the dependency-ordered, release-matched Raspberry Pi VC4 module
 * closure before running the exact DRM UAPI and SUBMIT_CL witness.  This
 * distinguishes missing guest modules from emulated-hardware failures.
 * The DT's V3D power-domain dependency is intentionally part of the witness:
 * firmware-domain failures must be resolved before a VC4 DRM node can exist.
 */
#define VC4_LINUX_V3D_SUBMIT_ENTRY vc4_linux_v3d_submit_base_main
#include "linux-v3d-submit-init.c"
#undef VC4_LINUX_V3D_SUBMIT_ENTRY

#include <stdlib.h>
#include <sys/syscall.h>

#define VC4_MODULE_MANIFEST "/etc/vc4-modules.manifest"

#define VC4_KMS_MAX_FBS         32U
#define VC4_KMS_MAX_CRTCS       16U
#define VC4_KMS_MAX_CONNECTORS  16U
#define VC4_KMS_MAX_ENCODERS    32U
#define VC4_KMS_MAX_MODES       64U
#define VC4_KMS_MAX_PROPERTIES  64U

#define VC4_DRM_MODE_CONNECTOR_WRITEBACK 18U
#define VC4_DRM_MODE_CONNECTED           1U

struct vc4_drm_mode_modeinfo {
    uint32_t clock;
    uint16_t hdisplay;
    uint16_t hsync_start;
    uint16_t hsync_end;
    uint16_t htotal;
    uint16_t hskew;
    uint16_t vdisplay;
    uint16_t vsync_start;
    uint16_t vsync_end;
    uint16_t vtotal;
    uint16_t vscan;
    uint32_t vrefresh;
    uint32_t flags;
    uint32_t type;
    char name[32];
};

struct vc4_drm_mode_card_res {
    uint64_t fb_id_ptr;
    uint64_t crtc_id_ptr;
    uint64_t connector_id_ptr;
    uint64_t encoder_id_ptr;
    uint32_t count_fbs;
    uint32_t count_crtcs;
    uint32_t count_connectors;
    uint32_t count_encoders;
    uint32_t min_width;
    uint32_t max_width;
    uint32_t min_height;
    uint32_t max_height;
};

struct vc4_drm_mode_get_connector {
    uint64_t encoders_ptr;
    uint64_t modes_ptr;
    uint64_t props_ptr;
    uint64_t prop_values_ptr;
    uint32_t count_modes;
    uint32_t count_props;
    uint32_t count_encoders;
    uint32_t encoder_id;
    uint32_t connector_id;
    uint32_t connector_type;
    uint32_t connector_type_id;
    uint32_t connection;
    uint32_t mm_width;
    uint32_t mm_height;
    uint32_t subpixel;
    uint32_t pad;
};

#define VC4_DRM_IOCTL_MODE_GETRESOURCES \
    VC4_DRM_IOWR(0xa0, struct vc4_drm_mode_card_res)
#define VC4_DRM_IOCTL_MODE_GETCONNECTOR \
    VC4_DRM_IOWR(0xa7, struct vc4_drm_mode_get_connector)

_Static_assert(sizeof(struct vc4_drm_mode_modeinfo) == 68,
               "unexpected DRM modeinfo UAPI layout");
_Static_assert(sizeof(struct vc4_drm_mode_card_res) == 64,
               "unexpected DRM resources UAPI layout");
_Static_assert(sizeof(struct vc4_drm_mode_get_connector) == 80,
               "unexpected DRM connector UAPI layout");

static int load_one_module(const char *path)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    int saved_errno;

    if (fd < 0) {
        report("VC4_LINUX_MODULE_OPEN_FAILED path=%s errno=%d (%s)\n",
               path, errno, strerror(errno));
        return -1;
    }
    if (syscall(SYS_finit_module, fd, "", 0) == 0) {
        close(fd);
        report("VC4_LINUX_MODULE_LOAD_OK path=%s\n", path);
        return 0;
    }
    saved_errno = errno;
    close(fd);
    if (saved_errno == EEXIST) {
        report("VC4_LINUX_MODULE_ALREADY_LOADED path=%s\n", path);
        return 0;
    }
    report("VC4_LINUX_MODULE_LOAD_FAILED path=%s errno=%d (%s)\n",
           path, saved_errno, strerror(saved_errno));
    errno = saved_errno;
    return -1;
}

static int load_vc4_module_manifest(void)
{
    char manifest[32768];
    ssize_t length;
    unsigned loaded = 0;
    unsigned failed = 0;
    int fd = open(VC4_MODULE_MANIFEST, O_RDONLY | O_CLOEXEC);

    marker("VC4_LINUX_MODULE_LOAD_START\n");
    if (fd < 0) {
        report("VC4_LINUX_MODULE_MANIFEST_MISSING path=%s errno=%d (%s)\n",
               VC4_MODULE_MANIFEST, errno, strerror(errno));
        return -1;
    }
    length = read(fd, manifest, sizeof(manifest) - 1);
    close(fd);
    if (length < 0) {
        report("VC4_LINUX_MODULE_MANIFEST_READ_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if ((size_t)length == sizeof(manifest) - 1) {
        marker("VC4_LINUX_MODULE_MANIFEST_TOO_LARGE\n");
        return -1;
    }
    manifest[length] = '\0';

    char *cursor = manifest;
    while (*cursor != '\0') {
        char *line = cursor;
        char *newline = strchr(cursor, '\n');

        if (newline != NULL) {
            *newline = '\0';
            cursor = newline + 1;
        } else {
            cursor += strlen(cursor);
        }
        while (*line == ' ' || *line == '\t') {
            line++;
        }
        size_t line_length = strlen(line);
        while (line_length > 0 &&
               (line[line_length - 1] == ' ' ||
                line[line_length - 1] == '\t' ||
                line[line_length - 1] == '\r')) {
            line[--line_length] = '\0';
        }
        if (*line == '\0' || *line == '#') {
            continue;
        }
        if (load_one_module(line) == 0) {
            loaded++;
        } else {
            failed++;
        }
    }

    report("VC4_LINUX_MODULE_LOAD_DONE loaded=%u failed=%u\n",
           loaded, failed);
    if (failed != 0) {
        return -1;
    }
    marker("VC4_LINUX_MODULE_CLOSURE_OK\n");
    return 0;
}

/*
 * Detailed report() records are best-effort diagnostics: a busy serial tty
 * can reject a nonblocking write.  marker() has a /dev/kmsg fallback, so
 * repeat every semantically important successful stage as a plain marker.
 * These markers are emitted only after the corresponding aggregate helper
 * has returned success; they do not weaken the witness.
 */
static void mark_module_success(void)
{
    marker("VC4_LINUX_MODULE_LOAD_DONE\n");
    marker("VC4_LINUX_MODULE_CLOSURE_OK\n");
}

static void mark_node_success(const VC4DRMNode *node,
                              const VC4DRMNode *card,
                              const VC4DRMNode *render)
{
    if (node == render) {
        marker("VC4_LINUX_DRM_RENDER128_OK\n");
    } else if (node == card) {
        marker("VC4_LINUX_DRM_CARD0_OK\n");
    }
}

static void mark_uapi_success(void)
{
    marker("VC4_LINUX_DRM_IDENT_OK\n");
    marker("VC4_LINUX_DRM_CREATE_BO_OK\n");
    marker("VC4_LINUX_DRM_MMAP_BO_OK\n");
    marker("VC4_LINUX_DRM_UAPI_OK\n");
}

static void mark_submit_success(void)
{
    marker("VC4_LINUX_DRM_SUBMIT_CL_OK\n");
    marker("VC4_LINUX_DRM_SUBMIT_WAIT_OK\n");
    marker("VC4_LINUX_DRM_SUBMIT_PIXELS_OK\n");
    marker("VC4_LINUX_DRM_SUBMIT_OK\n");
}

/*
 * The transient corrected-supplier probe injects drm_mode.h and the modeset
 * helper below this point.  Keep its declaration and userspace connection
 * spelling gated on that header so ordinary builds remain unchanged.
 */
#ifdef _DRM_MODE_H
#define DRM_MODE_CONNECTED VC4_DRM_MODE_CONNECTED
static int vc4_kms_modeset_probe(int fd);
#endif

static int probe_kms_topology(VC4DRMNode *card)
{
    struct vc4_drm_mode_card_res resources = { 0 };
    uint32_t fb_ids[VC4_KMS_MAX_FBS] = { 0 };
    uint32_t crtc_ids[VC4_KMS_MAX_CRTCS] = { 0 };
    uint32_t connector_ids[VC4_KMS_MAX_CONNECTORS] = { 0 };
    uint32_t encoder_ids[VC4_KMS_MAX_ENCODERS] = { 0 };
    unsigned physical_connectors = 0;
    unsigned connected_physical = 0;
    unsigned connected_physical_modes = 0;

    marker("VC4_LINUX_KMS_START\n");
    if (card->fd < 0 || !card->vc4) {
        marker("VC4_LINUX_KMS_FAILED stage=no-vc4-card\n");
        return -1;
    }
    if (ioctl(card->fd, VC4_DRM_IOCTL_MODE_GETRESOURCES, &resources) < 0) {
        report("VC4_LINUX_KMS_FAILED stage=get-resources-counts errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    report("VC4_LINUX_KMS_RESOURCES fbs=%u crtcs=%u connectors=%u encoders=%u min=%ux%u max=%ux%u\n",
           resources.count_fbs, resources.count_crtcs,
           resources.count_connectors, resources.count_encoders,
           resources.min_width, resources.min_height,
           resources.max_width, resources.max_height);
    marker("VC4_LINUX_KMS_RESOURCES_OK\n");

    if (resources.count_fbs > VC4_KMS_MAX_FBS ||
        resources.count_crtcs > VC4_KMS_MAX_CRTCS ||
        resources.count_connectors > VC4_KMS_MAX_CONNECTORS ||
        resources.count_encoders > VC4_KMS_MAX_ENCODERS) {
        marker("VC4_LINUX_KMS_FAILED stage=resource-capacity\n");
        return -1;
    }

    resources.fb_id_ptr = (uintptr_t)fb_ids;
    resources.crtc_id_ptr = (uintptr_t)crtc_ids;
    resources.connector_id_ptr = (uintptr_t)connector_ids;
    resources.encoder_id_ptr = (uintptr_t)encoder_ids;
    if (ioctl(card->fd, VC4_DRM_IOCTL_MODE_GETRESOURCES, &resources) < 0) {
        report("VC4_LINUX_KMS_FAILED stage=get-resources errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    if (resources.count_fbs > VC4_KMS_MAX_FBS ||
        resources.count_crtcs > VC4_KMS_MAX_CRTCS ||
        resources.count_connectors > VC4_KMS_MAX_CONNECTORS ||
        resources.count_encoders > VC4_KMS_MAX_ENCODERS) {
        marker("VC4_LINUX_KMS_FAILED stage=resource-race\n");
        return -1;
    }

    if (resources.count_crtcs != 0) {
        marker("VC4_LINUX_KMS_CRTC_OK\n");
    }
    if (resources.count_connectors != 0) {
        marker("VC4_LINUX_KMS_CONNECTOR_OBJECT_OK\n");
    }

    for (uint32_t index = 0; index < resources.count_connectors; index++) {
        struct vc4_drm_mode_get_connector connector = {
            .connector_id = connector_ids[index],
        };
        struct vc4_drm_mode_modeinfo modes[VC4_KMS_MAX_MODES] = { 0 };
        uint32_t encoders[VC4_KMS_MAX_ENCODERS] = { 0 };
        uint32_t properties[VC4_KMS_MAX_PROPERTIES] = { 0 };
        uint64_t property_values[VC4_KMS_MAX_PROPERTIES] = { 0 };
        bool physical;

        /* count_modes == 0 asks the DRM master to force-probe the sink. */
        if (ioctl(card->fd, VC4_DRM_IOCTL_MODE_GETCONNECTOR,
                  &connector) < 0) {
            report("VC4_LINUX_KMS_CONNECTOR_FAILED id=%u stage=probe errno=%d (%s)\n",
                   connector.connector_id, errno, strerror(errno));
            continue;
        }
        if (connector.count_modes > VC4_KMS_MAX_MODES ||
            connector.count_encoders > VC4_KMS_MAX_ENCODERS ||
            connector.count_props > VC4_KMS_MAX_PROPERTIES) {
            report("VC4_LINUX_KMS_CONNECTOR_FAILED id=%u stage=capacity modes=%u encoders=%u props=%u\n",
                   connector.connector_id, connector.count_modes,
                   connector.count_encoders, connector.count_props);
            continue;
        }

        connector.modes_ptr = (uintptr_t)modes;
        connector.encoders_ptr = (uintptr_t)encoders;
        connector.props_ptr = (uintptr_t)properties;
        connector.prop_values_ptr = (uintptr_t)property_values;
        if (ioctl(card->fd, VC4_DRM_IOCTL_MODE_GETCONNECTOR,
                  &connector) < 0) {
            report("VC4_LINUX_KMS_CONNECTOR_FAILED id=%u stage=read errno=%d (%s)\n",
                   connector.connector_id, errno, strerror(errno));
            continue;
        }
        if (connector.count_modes > VC4_KMS_MAX_MODES ||
            connector.count_encoders > VC4_KMS_MAX_ENCODERS ||
            connector.count_props > VC4_KMS_MAX_PROPERTIES) {
            report("VC4_LINUX_KMS_CONNECTOR_FAILED id=%u stage=hotplug-race modes=%u encoders=%u props=%u\n",
                   connector.connector_id, connector.count_modes,
                   connector.count_encoders, connector.count_props);
            continue;
        }

        physical = connector.connector_type !=
                   VC4_DRM_MODE_CONNECTOR_WRITEBACK;
        report("VC4_LINUX_KMS_CONNECTOR id=%u type=%u type_id=%u connection=%u encoder=%u modes=%u encoders=%u props=%u physical=%u\n",
               connector.connector_id, connector.connector_type,
               connector.connector_type_id, connector.connection,
               connector.encoder_id, connector.count_modes,
               connector.count_encoders, connector.count_props,
               physical ? 1U : 0U);
        if (connector.count_modes != 0) {
            report("VC4_LINUX_KMS_MODE connector=%u name=%.32s clock=%u size=%ux%u refresh=%u flags=0x%08x type=0x%08x\n",
                   connector.connector_id, modes[0].name, modes[0].clock,
                   modes[0].hdisplay, modes[0].vdisplay,
                   modes[0].vrefresh, modes[0].flags, modes[0].type);
        }

        if (!physical) {
            continue;
        }
        physical_connectors++;
        if (connector.connection == VC4_DRM_MODE_CONNECTED) {
            connected_physical++;
            connected_physical_modes += connector.count_modes;
        }
    }

    if (physical_connectors != 0) {
        marker("VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK\n");
    }
    if (connected_physical != 0) {
        marker("VC4_LINUX_KMS_CONNECTED_OK\n");
    }
    if (connected_physical_modes != 0) {
        marker("VC4_LINUX_KMS_MODE_OK\n");
    }
    report("VC4_LINUX_KMS_DONE crtcs=%u connector_objects=%u physical=%u connected=%u modes=%u\n",
           resources.count_crtcs, resources.count_connectors,
           physical_connectors, connected_physical,
           connected_physical_modes);
    marker("VC4_LINUX_KMS_PROBE_DONE\n");

    if (resources.count_crtcs != 0 && physical_connectors != 0 &&
        connected_physical != 0 && connected_physical_modes != 0) {
        marker("VC4_LINUX_KMS_TOPOLOGY_OK\n");
        return 0;
    }

    marker("VC4_LINUX_KMS_PARTIAL\n");
    return -1;
}

int main(void)
{
    struct timespec settle = {
        .tv_sec = 2,
    };
    VC4DRMNode card;
    VC4DRMNode render;
    VC4DRMNode *selected = NULL;
    int module_result;
    int uapi_result = -1;
    int submit_result = -1;
    int kms_result = -1;
    int framebuffer_result;

    prepare_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_V3D_MODULAR_START\n");
    module_result = load_vc4_module_manifest();
    if (module_result == 0) {
        mark_module_success();
    }
    nanosleep(&settle, NULL);
    report_topology();

    card = open_drm_node("CARD0", "/dev/dri/card0");
    render = open_drm_node("RENDER128", "/dev/dri/renderD128");
    if (card.fd >= 0 && card.vc4) {
        kms_result = probe_kms_topology(&card);
    } else {
        marker("VC4_LINUX_KMS_FAILED stage=no-vc4-card\n");
    }
    if (render.fd >= 0 && render.vc4) {
        selected = &render;
    } else if (card.fd >= 0 && card.vc4) {
        selected = &card;
    }
    if (selected != NULL) {
        mark_node_success(selected, &card, &render);
        uapi_result = probe_vc4_uapi(selected);
        if (uapi_result == 0) {
            mark_uapi_success();
            submit_result = submit_clear_job(selected);
            if (submit_result == 0) {
                mark_submit_success();
            }
        }
    } else {
        marker("VC4_LINUX_DRM_SUBMIT_SKIPPED no-vc4-node\n");
    }

    framebuffer_result = paint_framebuffer();
    report("VC4_LINUX_V3D_MODULAR_DONE modules=%d card0=%d render128=%d uapi=%d submit=%d kms=%d framebuffer=%d\n",
           module_result,
           card.fd >= 0 && card.vc4 ? 0 : -1,
           render.fd >= 0 && render.vc4 ? 0 : -1,
           uapi_result, submit_result, kms_result, framebuffer_result);
    marker("VC4_LINUX_V3D_MODULAR_DONE\n");
    if (module_result == 0 && submit_result == 0) {
        marker("VC4_LINUX_V3D_MODULAR_OK\n");
    } else {
        marker("VC4_LINUX_V3D_MODULAR_PARTIAL\n");
    }
    if (framebuffer_result == 0) {
        marker("VC4_LINUX_FB_OK\n");
    }

    if (render.fd >= 0) {
        close(render.fd);
    }
    if (card.fd >= 0) {
        close(card.fd);
    }
    marker("VC4_LINUX_INIT_IDLE\n");
    for (;;) {
        pause();
    }
}
