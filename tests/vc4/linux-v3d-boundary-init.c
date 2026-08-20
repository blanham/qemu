/*
 * Follow-on Linux VC4 boundary witness.
 *
 * Reuse the complete VERSION/GET_PARAM/GEM/mmap/framebuffer implementation
 * from the first UAPI probe, then add explicit platform-binding evidence and
 * the idle-BO wait ioctl.  Keeping this as a textual include makes the two
 * witnesses share one UAPI definition rather than slowly diverging.
 */
#define main vc4_linux_v3d_uapi_base_main
#include "linux-v3d-uapi-init.c"
#undef main

struct vc4_drm_wait_bo {
    uint32_t handle;
    uint32_t pad;
    uint64_t timeout_ns;
};

#define VC4_DRM_IOCTL_WAIT_BO \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x02, struct vc4_drm_wait_bo)
#define VC4_BOUNDARY_WAIT_NS UINT64_C(1000000000)

_Static_assert(sizeof(struct vc4_drm_wait_bo) == 16,
               "unexpected VC4 WAIT_BO UAPI layout");

static bool path_exists(const char *path)
{
    struct stat status;

    return stat(path, &status) == 0;
}

static void mount_debugfs(void)
{
    (void)mkdir("/sys/kernel/debug", 0755);
    if (mount("debugfs", "/sys/kernel/debug", "debugfs",
              MS_NOSUID | MS_NODEV | MS_NOEXEC, NULL) == 0) {
        marker("VC4_LINUX_DEBUGFS_OK\n");
    } else if (errno == EBUSY) {
        marker("VC4_LINUX_DEBUGFS_ALREADY_MOUNTED\n");
    } else {
        report("VC4_LINUX_DEBUGFS_FAILED errno=%d (%s)\n",
               errno, strerror(errno));
    }
}

static void dump_file(const char *label, const char *path, size_t limit)
{
    char buffer[1024];
    size_t total = 0;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);

    if (fd < 0) {
        report("VC4_LINUX_DUMP_MISSING label=%s path=%s errno=%d (%s)\n",
               label, path, errno, strerror(errno));
        return;
    }

    report("VC4_LINUX_DUMP_BEGIN label=%s path=%s\n", label, path);
    while (total < limit) {
        size_t remaining = limit - total;
        size_t request = remaining < sizeof(buffer) ? remaining :
                         sizeof(buffer);
        ssize_t length = read(fd, buffer, request);

        if (length == 0) {
            break;
        }
        if (length < 0) {
            if (errno == EINTR) {
                continue;
            }
            report("\nVC4_LINUX_DUMP_READ_FAILED label=%s errno=%d (%s)\n",
                   label, errno, strerror(errno));
            break;
        }
        for (ssize_t index = 0; index < length; index++) {
            if (buffer[index] == '\0') {
                buffer[index] = '\n';
            }
        }
        (void)write_all(STDOUT_FILENO, buffer, (size_t)length);
        total += (size_t)length;
    }
    close(fd);
    report("\nVC4_LINUX_DUMP_END label=%s bytes=%zu\n", label, total);
}

static void report_binding_evidence(const char *phase)
{
    report("VC4_LINUX_BIND_EVIDENCE_BEGIN phase=%s\n", phase);
    report_topology();
    report_path("V3D_BOUND_DRIVER",
                "/sys/bus/platform/devices/3fc00000.v3d/driver");
    report_path("V3D_OF_NODE",
                "/sys/bus/platform/devices/3fc00000.v3d/of_node");
    dump_file("V3D_UEVENT",
              "/sys/bus/platform/devices/3fc00000.v3d/uevent", 8192);
    dump_file("V3D_MODALIAS",
              "/sys/bus/platform/devices/3fc00000.v3d/modalias", 4096);
    dump_file("DEVICES_DEFERRED",
              "/sys/kernel/debug/devices_deferred", 32768);
    dump_file("DEVICE_COMPONENT",
              "/sys/kernel/debug/device_component", 32768);
    dump_file("CLOCK_SUMMARY",
              "/sys/kernel/debug/clk/clk_summary", 65536);
    dump_file("INTERRUPTS", "/proc/interrupts", 32768);
    report("VC4_LINUX_BIND_EVIDENCE_END phase=%s\n", phase);
}

static int try_platform_bind(void)
{
    static const char device[] = "3fc00000.v3d";
    static const char *const candidates[] = {
        "/sys/bus/platform/drivers/vc4_v3d/bind",
        "/sys/bus/platform/drivers/v3d/bind",
    };

    if (path_exists("/sys/bus/platform/devices/3fc00000.v3d/driver")) {
        marker("VC4_LINUX_V3D_ALREADY_BOUND\n");
        return 0;
    }

    for (unsigned index = 0;
         index < sizeof(candidates) / sizeof(candidates[0]); index++) {
        int fd = open(candidates[index], O_WRONLY | O_CLOEXEC);

        if (fd < 0) {
            report("VC4_LINUX_V3D_BIND_PATH_MISSING path=%s errno=%d (%s)\n",
                   candidates[index], errno, strerror(errno));
            continue;
        }
        if (write_all(fd, device, sizeof(device) - 1) == 0) {
            close(fd);
            report("VC4_LINUX_V3D_BIND_WRITE_OK path=%s device=%s\n",
                   candidates[index], device);
            return 0;
        }
        report("VC4_LINUX_V3D_BIND_WRITE_FAILED path=%s errno=%d (%s)\n",
               candidates[index], errno, strerror(errno));
        close(fd);
    }
    return -1;
}

static int probe_wait_bo(VC4DRMNode *node)
{
    struct vc4_drm_create_bo create = {
        .size = VC4_DRM_TEST_BO_SIZE,
    };
    struct vc4_drm_wait_bo wait = {
        .timeout_ns = VC4_BOUNDARY_WAIT_NS,
    };
    struct vc4_drm_gem_close close_bo = { 0 };
    int result = -1;

    marker("VC4_LINUX_DRM_WAIT_BO_START\n");
    if (ioctl(node->fd, VC4_DRM_IOCTL_CREATE_BO, &create) < 0) {
        report("VC4_LINUX_DRM_WAIT_BO_FAILED stage=create-bo errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    close_bo.handle = create.handle;
    wait.handle = create.handle;
    if (ioctl(node->fd, VC4_DRM_IOCTL_WAIT_BO, &wait) < 0) {
        report("VC4_LINUX_DRM_WAIT_BO_FAILED stage=wait-bo handle=%u errno=%d (%s)\n",
               wait.handle, errno, strerror(errno));
        goto out;
    }
    report("VC4_LINUX_DRM_WAIT_BO_OK handle=%u timeout_ns=%llu\n",
           wait.handle, (unsigned long long)wait.timeout_ns);
    result = 0;

out:
    (void)ioctl(node->fd, VC4_DRM_IOCTL_GEM_CLOSE, &close_bo);
    return result;
}

int main(void)
{
    struct timespec settle = {
        .tv_sec = 1,
    };
    VC4DRMNode card;
    VC4DRMNode render;
    VC4DRMNode *selected = NULL;
    int bind_result;
    int uapi_result = -1;
    int wait_result = -1;
    int framebuffer_result;

    prepare_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_V3D_BOUNDARY_START\n");
    mount_debugfs();
    report_binding_evidence("before-bind");
    bind_result = try_platform_bind();
    nanosleep(&settle, NULL);
    report_binding_evidence("after-bind");

    card = open_drm_node("CARD0", "/dev/dri/card0");
    render = open_drm_node("RENDER128", "/dev/dri/renderD128");
    if (render.fd >= 0 && render.vc4) {
        selected = &render;
    } else if (card.fd >= 0 && card.vc4) {
        selected = &card;
    }
    if (selected != NULL) {
        uapi_result = probe_vc4_uapi(selected);
        wait_result = probe_wait_bo(selected);
    } else {
        marker("VC4_LINUX_DRM_UAPI_SKIPPED no-vc4-node\n");
    }

    framebuffer_result = paint_framebuffer();
    report_binding_evidence("final");
    report("VC4_LINUX_V3D_BOUNDARY_DONE bind=%d card0=%d render128=%d uapi=%d wait_bo=%d framebuffer=%d\n",
           bind_result,
           card.fd >= 0 && card.vc4 ? 0 : -1,
           render.fd >= 0 && render.vc4 ? 0 : -1,
           uapi_result, wait_result, framebuffer_result);
    if (uapi_result == 0 && wait_result == 0) {
        marker("VC4_LINUX_V3D_BOUNDARY_OK\n");
    } else {
        marker("VC4_LINUX_V3D_BOUNDARY_PARTIAL\n");
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
