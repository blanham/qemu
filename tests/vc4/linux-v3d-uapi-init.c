#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

struct vc4_drm_version {
    int version_major;
    int version_minor;
    int version_patchlevel;
    size_t name_len;
    char *name;
    size_t date_len;
    char *date;
    size_t desc_len;
    char *desc;
};

struct vc4_drm_get_param {
    uint32_t param;
    uint32_t pad;
    uint64_t value;
};

struct vc4_drm_create_bo {
    uint32_t size;
    uint32_t flags;
    uint32_t handle;
    uint32_t pad;
};

struct vc4_drm_mmap_bo {
    uint32_t handle;
    uint32_t flags;
    uint64_t offset;
};

struct vc4_drm_gem_close {
    uint32_t handle;
    uint32_t pad;
};

#define VC4_DRM_IOCTL_BASE              'd'
#define VC4_DRM_COMMAND_BASE            0x40
#define VC4_DRM_IOW(nr, type)           _IOW(VC4_DRM_IOCTL_BASE, nr, type)
#define VC4_DRM_IOWR(nr, type)          _IOWR(VC4_DRM_IOCTL_BASE, nr, type)
#define VC4_DRM_IOCTL_VERSION           \
    VC4_DRM_IOWR(0x00, struct vc4_drm_version)
#define VC4_DRM_IOCTL_GEM_CLOSE         \
    VC4_DRM_IOW(0x09, struct vc4_drm_gem_close)
#define VC4_DRM_IOCTL_CREATE_BO         \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x03, struct vc4_drm_create_bo)
#define VC4_DRM_IOCTL_MMAP_BO           \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x04, struct vc4_drm_mmap_bo)
#define VC4_DRM_IOCTL_GET_PARAM         \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x07, struct vc4_drm_get_param)

#define VC4_DRM_PARAM_V3D_IDENT0        0
#define VC4_DRM_PARAM_V3D_IDENT1        1
#define VC4_DRM_PARAM_V3D_IDENT2        2
#define VC4_V3D_EXPECTED_IDENT0         UINT64_C(0x02443356)
#define VC4_DRM_TEST_BO_SIZE            4096
#define VC4_DRM_SENTINEL_HEAD           UINT32_C(0x56433447)
#define VC4_DRM_SENTINEL_TAIL           UINT32_C(0x55415049)

_Static_assert(sizeof(struct vc4_drm_version) == 64,
               "unexpected DRM version UAPI layout");
_Static_assert(sizeof(struct vc4_drm_get_param) == 16,
               "unexpected VC4 GET_PARAM UAPI layout");
_Static_assert(sizeof(struct vc4_drm_create_bo) == 16,
               "unexpected VC4 CREATE_BO UAPI layout");
_Static_assert(sizeof(struct vc4_drm_mmap_bo) == 16,
               "unexpected VC4 MMAP_BO UAPI layout");
_Static_assert(sizeof(struct vc4_drm_gem_close) == 8,
               "unexpected DRM GEM_CLOSE UAPI layout");

typedef struct VC4DRMNode {
    int fd;
    bool vc4;
    const char *label;
    char name[64];
} VC4DRMNode;

static int write_all(int fd, const char *text, size_t length)
{
    while (length != 0) {
        ssize_t written = write(fd, text, length);

        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        text += written;
        length -= (size_t)written;
    }
    return 0;
}

static void marker(const char *text)
{
    int saved_errno = errno;
    size_t length = strlen(text);

    if (write_all(STDOUT_FILENO, text, length) < 0) {
        int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);

        if (fd >= 0) {
            (void)write_all(fd, text, length);
            close(fd);
        }
    }
    errno = saved_errno;
}

static void report(const char *format, ...)
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
    (void)write_all(STDOUT_FILENO, buffer, (size_t)length);
}

static void reopen_console(void)
{
    static const char *const candidates[] = {
        "/dev/console",
        "/dev/ttyAMA1",
        "/dev/ttyAMA0",
    };
    int fd = -1;

    for (unsigned index = 0;
         index < sizeof(candidates) / sizeof(candidates[0]); index++) {
        fd = open(candidates[index], O_RDWR | O_CLOEXEC);
        if (fd >= 0) {
            break;
        }
    }
    if (fd < 0) {
        return;
    }
    for (int target = STDIN_FILENO; target <= STDERR_FILENO; target++) {
        if (fd != target) {
            (void)dup2(fd, target);
        }
    }
    if (fd > STDERR_FILENO) {
        close(fd);
    }
}

static void prepare_filesystems(void)
{
    (void)mkdir("/dev", 0755);
    (void)mkdir("/proc", 0555);
    (void)mkdir("/sys", 0555);
    (void)mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID, "mode=0755");
    reopen_console();
    (void)mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC,
                NULL);
    (void)mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC,
                NULL);
}

static void report_path(const char *label, const char *path)
{
    struct stat status;

    if (stat(path, &status) == 0) {
        report("VC4_LINUX_PATH_OK label=%s path=%s mode=%o major=%u minor=%u\n",
               label, path, status.st_mode & 07777,
               S_ISCHR(status.st_mode) ? major(status.st_rdev) : 0,
               S_ISCHR(status.st_mode) ? minor(status.st_rdev) : 0);
    } else {
        report("VC4_LINUX_PATH_MISSING label=%s path=%s errno=%d (%s)\n",
               label, path, errno, strerror(errno));
    }
}

static void report_text(const char *label, const char *path)
{
    char buffer[256];
    ssize_t length;
    int fd = open(path, O_RDONLY | O_CLOEXEC);

    if (fd < 0) {
        report("VC4_LINUX_TEXT_MISSING label=%s path=%s errno=%d (%s)\n",
               label, path, errno, strerror(errno));
        return;
    }
    length = read(fd, buffer, sizeof(buffer) - 1);
    close(fd);
    if (length < 0) {
        report("VC4_LINUX_TEXT_FAILED label=%s path=%s errno=%d (%s)\n",
               label, path, errno, strerror(errno));
        return;
    }
    while (length > 0 &&
           (buffer[length - 1] == '\0' || buffer[length - 1] == '\n' ||
            buffer[length - 1] == '\r')) {
        length--;
    }
    buffer[length] = '\0';
    report("VC4_LINUX_TEXT_OK label=%s value=%s\n", label, buffer);
}

static void report_topology(void)
{
    report_path("DRI_CLASS", "/sys/class/drm");
    report_path("DRM_CARD0", "/sys/class/drm/card0");
    report_path("DRM_RENDER128", "/sys/class/drm/renderD128");
    report_path("VC4_DRIVER", "/sys/bus/platform/drivers/vc4");
    report_path("VC4_V3D_DRIVER", "/sys/bus/platform/drivers/vc4_v3d");
    report_path("V3D_DRIVER", "/sys/bus/platform/drivers/v3d");
    report_path("V3D_DEVICE", "/sys/bus/platform/devices/3fc00000.v3d");
    report_text("DT_V3D_STATUS",
                "/proc/device-tree/soc/v3d@7ec00000/status");
}

static int wait_open(const char *path, int flags, unsigned attempts)
{
    struct timespec delay = { .tv_nsec = 100000000 };
    int saved_errno = ENOENT;

    for (unsigned attempt = 0; attempt < attempts; attempt++) {
        int fd = open(path, flags | O_CLOEXEC);

        if (fd >= 0) {
            return fd;
        }
        saved_errno = errno;
        nanosleep(&delay, NULL);
    }
    errno = saved_errno;
    return -1;
}

static VC4DRMNode open_drm_node(const char *label, const char *path)
{
    VC4DRMNode node = { .fd = -1, .label = label };
    struct vc4_drm_version version;
    char date[64];
    char description[192];
    struct stat status;

    node.fd = wait_open(path, O_RDWR, 100);
    if (node.fd < 0) {
        report("VC4_LINUX_DRM_NODE_MISSING label=%s path=%s errno=%d (%s)\n",
               label, path, errno, strerror(errno));
        return node;
    }
    memset(&status, 0, sizeof(status));
    if (fstat(node.fd, &status) < 0) {
        close(node.fd);
        node.fd = -1;
        return node;
    }

    memset(&version, 0, sizeof(version));
    memset(node.name, 0, sizeof(node.name));
    memset(date, 0, sizeof(date));
    memset(description, 0, sizeof(description));
    version.name_len = sizeof(node.name) - 1;
    version.name = node.name;
    version.date_len = sizeof(date) - 1;
    version.date = date;
    version.desc_len = sizeof(description) - 1;
    version.desc = description;
    if (ioctl(node.fd, VC4_DRM_IOCTL_VERSION, &version) < 0) {
        report("VC4_LINUX_DRM_VERSION_FAILED label=%s errno=%d (%s)\n",
               label, errno, strerror(errno));
        close(node.fd);
        node.fd = -1;
        return node;
    }

    node.name[sizeof(node.name) - 1] = '\0';
    node.vc4 = strcmp(node.name, "vc4") == 0;
    if (strcmp(label, "CARD0") == 0) {
        report("VC4_LINUX_DRM_CARD0_OK major=%u minor=%u name=%s version=%d.%d.%d\n",
               major(status.st_rdev), minor(status.st_rdev), node.name,
               version.version_major, version.version_minor,
               version.version_patchlevel);
    } else {
        report("VC4_LINUX_DRM_RENDER128_OK major=%u minor=%u name=%s version=%d.%d.%d\n",
               major(status.st_rdev), minor(status.st_rdev), node.name,
               version.version_major, version.version_minor,
               version.version_patchlevel);
    }
    if (!node.vc4) {
        report("VC4_LINUX_DRM_WRONG_DRIVER label=%s expected=vc4 actual=%s\n",
               label, node.name);
    }
    return node;
}

static int probe_vc4_uapi(VC4DRMNode *node)
{
    static const uint32_t params[] = {
        VC4_DRM_PARAM_V3D_IDENT0,
        VC4_DRM_PARAM_V3D_IDENT1,
        VC4_DRM_PARAM_V3D_IDENT2,
    };
    struct vc4_drm_create_bo create = { .size = VC4_DRM_TEST_BO_SIZE };
    struct vc4_drm_mmap_bo map = { 0 };
    struct vc4_drm_gem_close close_bo = { 0 };
    volatile uint32_t *words = MAP_FAILED;
    uint64_t identities[3] = { 0 };
    int result = -1;

    marker("VC4_LINUX_DRM_UAPI_START\n");
    for (unsigned index = 0;
         index < sizeof(params) / sizeof(params[0]); index++) {
        struct vc4_drm_get_param request = { .param = params[index] };

        if (ioctl(node->fd, VC4_DRM_IOCTL_GET_PARAM, &request) < 0) {
            report("VC4_LINUX_DRM_UAPI_FAILED stage=get-param param=%u errno=%d (%s)\n",
                   request.param, errno, strerror(errno));
            goto out;
        }
        identities[index] = request.value;
        report("VC4_LINUX_DRM_PARAM_OK param=%u value=0x%016llx\n",
               request.param, (unsigned long long)request.value);
    }
    if (identities[0] != VC4_V3D_EXPECTED_IDENT0) {
        report("VC4_LINUX_DRM_UAPI_FAILED stage=ident0 actual=0x%016llx\n",
               (unsigned long long)identities[0]);
        goto out;
    }
    marker("VC4_LINUX_DRM_IDENT_OK\n");

    if (ioctl(node->fd, VC4_DRM_IOCTL_CREATE_BO, &create) < 0) {
        report("VC4_LINUX_DRM_UAPI_FAILED stage=create-bo errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }
    if (create.handle == 0) {
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=create-bo-zero-handle\n");
        goto out;
    }
    close_bo.handle = create.handle;
    report("VC4_LINUX_DRM_CREATE_BO_OK handle=%u size=%u\n",
           create.handle, create.size);

    map.handle = create.handle;
    if (ioctl(node->fd, VC4_DRM_IOCTL_MMAP_BO, &map) < 0) {
        report("VC4_LINUX_DRM_UAPI_FAILED stage=mmap-bo errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }
    report("VC4_LINUX_DRM_MMAP_BO_OK handle=%u offset=0x%016llx\n",
           map.handle, (unsigned long long)map.offset);
    words = mmap(NULL, VC4_DRM_TEST_BO_SIZE, PROT_READ | PROT_WRITE,
                 MAP_SHARED, node->fd, (off_t)map.offset);
    if (words == MAP_FAILED) {
        report("VC4_LINUX_DRM_UAPI_FAILED stage=mmap errno=%d (%s)\n",
               errno, strerror(errno));
        goto out;
    }

    words[0] = VC4_DRM_SENTINEL_HEAD;
    words[VC4_DRM_TEST_BO_SIZE / sizeof(*words) - 1] = VC4_DRM_SENTINEL_TAIL;
    __sync_synchronize();
    (void)msync((void *)words, VC4_DRM_TEST_BO_SIZE, MS_SYNC);
    if (words[0] != VC4_DRM_SENTINEL_HEAD ||
        words[VC4_DRM_TEST_BO_SIZE / sizeof(*words) - 1] !=
        VC4_DRM_SENTINEL_TAIL) {
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=bo-coherency\n");
        goto out;
    }
    report("VC4_LINUX_DRM_GEM_MEMORY_OK head=0x%08x tail=0x%08x\n",
           words[0], words[VC4_DRM_TEST_BO_SIZE / sizeof(*words) - 1]);
    marker("VC4_LINUX_DRM_UAPI_OK\n");
    result = 0;

out:
    if (words != MAP_FAILED) {
        (void)munmap((void *)words, VC4_DRM_TEST_BO_SIZE);
    }
    if (close_bo.handle != 0) {
        (void)ioctl(node->fd, VC4_DRM_IOCTL_GEM_CLOSE, &close_bo);
    }
    return result;
}

static uint32_t channel_bits(uint8_t value, struct fb_bitfield field)
{
    uint64_t maximum;
    uint64_t scaled;

    if (field.length == 0) {
        return 0;
    }
    maximum = field.length >= 32 ? UINT32_MAX :
              ((UINT64_C(1) << field.length) - 1);
    scaled = ((uint64_t)value * maximum + 127) / 255;
    return (uint32_t)(scaled << field.offset);
}

static uint32_t make_pixel(const struct fb_var_screeninfo *var,
                           uint8_t red, uint8_t green, uint8_t blue)
{
    return channel_bits(red, var->red) |
           channel_bits(green, var->green) |
           channel_bits(blue, var->blue) |
           channel_bits(255, var->transp);
}

static int paint_framebuffer(void)
{
    struct fb_fix_screeninfo fix;
    struct fb_var_screeninfo var;
    volatile uint8_t *framebuffer;
    uint32_t colors[4];
    unsigned bytes_per_pixel;
    unsigned width;
    unsigned height;
    int fd = wait_open("/dev/fb0", O_RDWR, 50);

    if (fd < 0) {
        report("VC4_LINUX_FB_MISSING errno=%d (%s)\n",
               errno, strerror(errno));
        return -1;
    }
    memset(&fix, 0, sizeof(fix));
    memset(&var, 0, sizeof(var));
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) < 0 ||
        ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0) {
        close(fd);
        return -1;
    }
    bytes_per_pixel = (var.bits_per_pixel + 7) / 8;
    if (bytes_per_pixel == 0 || bytes_per_pixel > 4 ||
        fix.line_length == 0 || fix.smem_len == 0) {
        close(fd);
        errno = EINVAL;
        return -1;
    }
    width = var.xres;
    height = var.yres;
    if ((uint64_t)width * bytes_per_pixel > fix.line_length) {
        width = fix.line_length / bytes_per_pixel;
    }
    if ((uint64_t)height * fix.line_length > fix.smem_len) {
        height = fix.smem_len / fix.line_length;
    }
    framebuffer = mmap(NULL, fix.smem_len, PROT_READ | PROT_WRITE,
                       MAP_SHARED, fd, 0);
    if (framebuffer == MAP_FAILED) {
        close(fd);
        return -1;
    }
    colors[0] = make_pixel(&var, 255, 0, 0);
    colors[1] = make_pixel(&var, 0, 255, 0);
    colors[2] = make_pixel(&var, 0, 0, 255);
    colors[3] = make_pixel(&var, 255, 255, 255);
    for (unsigned y = 0; y < height; y++) {
        volatile uint8_t *row = framebuffer + (size_t)y * fix.line_length;
        unsigned vertical = y >= height / 2;

        for (unsigned x = 0; x < width; x++) {
            uint32_t pixel = colors[vertical * 2 + (x >= width / 2)];
            volatile uint8_t *address = row + (size_t)x * bytes_per_pixel;

            for (unsigned byte = 0; byte < bytes_per_pixel; byte++) {
                address[byte] = pixel >> (byte * 8);
            }
        }
    }
    (void)msync((void *)framebuffer, fix.smem_len, MS_SYNC);
    report("VC4_LINUX_FB_OK xres=%u yres=%u bpp=%u pitch=%u bytes=%u\n",
           width, height, var.bits_per_pixel, fix.line_length, fix.smem_len);
    (void)munmap((void *)framebuffer, fix.smem_len);
    close(fd);
    return 0;
}

int main(void)
{
    VC4DRMNode card;
    VC4DRMNode render;
    VC4DRMNode *selected = NULL;
    int uapi_result = -1;
    int framebuffer_result;

    prepare_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_DRM_PROBE_START\n");
    report_topology();

    card = open_drm_node("CARD0", "/dev/dri/card0");
    render = open_drm_node("RENDER128", "/dev/dri/renderD128");
    if (render.fd >= 0 && render.vc4) {
        selected = &render;
    } else if (card.fd >= 0 && card.vc4) {
        selected = &card;
    }
    if (selected != NULL) {
        uapi_result = probe_vc4_uapi(selected);
    } else {
        marker("VC4_LINUX_DRM_UAPI_SKIPPED no-vc4-node\n");
    }
    framebuffer_result = paint_framebuffer();
    report_topology();
    report("VC4_LINUX_DRM_PROBE_DONE card0=%d render128=%d uapi=%d framebuffer=%d\n",
           card.fd >= 0 && card.vc4 ? 0 : -1,
           render.fd >= 0 && render.vc4 ? 0 : -1,
           uapi_result, framebuffer_result);
    if (uapi_result == 0) {
        marker("VC4_LINUX_V3D_DRIVER_OK\n");
    } else if (selected != NULL) {
        marker("VC4_LINUX_V3D_DRIVER_PARTIAL\n");
    } else {
        marker("VC4_LINUX_V3D_DRIVER_MISSING\n");
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
