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

#include <sys/syscall.h>

#define VC4_MODULE_MANIFEST "/etc/vc4-modules.manifest"

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
    report("VC4_LINUX_V3D_MODULAR_DONE modules=%d card0=%d render128=%d uapi=%d submit=%d framebuffer=%d\n",
           module_result,
           card.fd >= 0 && card.vc4 ? 0 : -1,
           render.fd >= 0 && render.vc4 ? 0 : -1,
           uapi_result, submit_result, framebuffer_result);
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
