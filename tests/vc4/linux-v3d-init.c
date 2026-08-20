#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

/*
 * Keep the probe independent of libdrm so the initramfs remains a single
 * deterministic static binary.  These layouts and command numbers are the
 * stable DRM/VC4 UAPI from include/uapi/drm/{drm,vc4_drm}.h.
 */
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

#define VC4_DRM_IOCTL_BASE       'd'
#define VC4_DRM_COMMAND_BASE     0x40
#define VC4_DRM_IOWR(nr, type)   _IOWR(VC4_DRM_IOCTL_BASE, nr, type)
#define VC4_DRM_IOCTL_VERSION \
    VC4_DRM_IOWR(0x00, struct vc4_drm_version)
#define VC4_DRM_IOCTL_CREATE_BO \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x03, struct vc4_drm_create_bo)
#define VC4_DRM_IOCTL_MMAP_BO \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x04, struct vc4_drm_mmap_bo)
#define VC4_DRM_IOCTL_GET_PARAM \
    VC4_DRM_IOWR(VC4_DRM_COMMAND_BASE + 0x07, struct vc4_drm_get_param)

#define VC4_PARAM_V3D_IDENT0                 0
#define VC4_PARAM_V3D_IDENT1                 1
#define VC4_PARAM_V3D_IDENT2                 2
#define VC4_PARAM_SUPPORTS_BRANCHES          3
#define VC4_PARAM_SUPPORTS_ETC1              4
#define VC4_PARAM_SUPPORTS_THREADED_FS       5
#define VC4_PARAM_SUPPORTS_FIXED_RCL_ORDER   6
#define VC4_PARAM_SUPPORTS_MADVISE           7
#define VC4_PARAM_SUPPORTS_PERFMON           8

#define VC4_EXPECTED_IDENT0 UINT64_C(0x02443356)
#define VC4_PROBE_BO_SIZE   4096U

_Static_assert(sizeof(struct vc4_drm_get_param) == 16,
               "unexpected drm_vc4_get_param ABI");
_Static_assert(sizeof(struct vc4_drm_create_bo) == 16,
               "unexpected drm_vc4_create_bo ABI");
_Static_assert(sizeof(struct vc4_drm_mmap_bo) == 16,
               "unexpected drm_vc4_mmap_bo ABI");

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
    size_t length = strlen(text);
    int saved_errno = errno;

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
    char buffer[512];
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);

    if (length <= 0) {
        return;
    }
    if ((size_t)length >= sizeof(buffer)) {
        length = sizeof(buffer) - 1;
    }
    marker(buffer);
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

static void prepare_pseudo_filesystems(void)
{
    (void)mkdir("/dev", 0755);
    (void)mkdir("/proc", 0555);
    (void)mkdir("/sys", 0555);
    (void)mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID,
                "mode=0755");
    reopen_console();
    (void)mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC,
                NULL);
    (void)mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC,
                NULL);
}

static bool path_exists(const char *path)
{
    struct stat status;

    return stat(path, &status) == 0;
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

static void report_text_file(const char *label, const char *path)
{
    char buffer[256];
    ssize_t length;
    int fd;

    fd = open(path, O_RDONLY | O_CLOEXEC);
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

static int wait_open(const char *path, int flags, unsigned attempts)
{
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = 100000000,
    };
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

static int query_drm_node(const char *label, const char *path, int *fd_out)
{
    struct vc4_drm_version version;
    char name[64];
    char date[64];
    char description[192];
    struct stat status;
    int fd;

    fd = wait_open(path, O_RDWR, 150);
    if (fd < 0) {
        report("VC4_LINUX_DRM_%s_MISSING path=%s errno=%d (%s)\n",
               label, path, errno, strerror(errno));
        return -1;
    }

    memset(&status, 0, sizeof(status));
    if (fstat(fd, &status) < 0) {
        report("VC4_LINUX_DRM_%s_FSTAT_FAILED errno=%d (%s)\n",
               label, errno, strerror(errno));
        close(fd);
        return -1;
    }

    memset(&version, 0, sizeof(version));
    memset(name, 0, sizeof(name));
    memset(date, 0, sizeof(date));
    memset(description, 0, sizeof(description));
    version.name_len = sizeof(name) - 1;
    version.name = name;
    version.date_len = sizeof(date) - 1;
    version.date = date;
    version.desc_len = sizeof(description) - 1;
    version.desc = description;

    if (ioctl(fd, VC4_DRM_IOCTL_VERSION, &version) < 0) {
        report("VC4_LINUX_DRM_%s_IOCTL_FAILED major=%u minor=%u errno=%d (%s)\n",
               label, major(status.st_rdev), minor(status.st_rdev),
               errno, strerror(errno));
        close(fd);
        return -1;
    }

    name[sizeof(name) - 1] = '\0';
    date[sizeof(date) - 1] = '\0';
    description[sizeof(description) - 1] = '\0';
    report("VC4_LINUX_DRM_%s_OK major=%u minor=%u name=%s version=%d.%d.%d date=%s desc=%s\n",
           label, major(status.st_rdev), minor(status.st_rdev), name,
           version.version_major, version.version_minor,
           version.version_patchlevel, date, description);

    if (strcmp(name, "vc4") != 0) {
        close(fd);
        return 1;
    }
    if (fd_out != NULL) {
        *fd_out = fd;
    } else {
        close(fd);
    }
    return 0;
}

static int vc4_get_param(int fd, uint32_t param, const char *name,
                         bool required, uint64_t *value_out)
{
    struct vc4_drm_get_param request = {
        .param = param,
    };

    if (ioctl(fd, VC4_DRM_IOCTL_GET_PARAM, &request) < 0) {
        report("VC4_LINUX_DRM_PARAM_FAILED name=%s id=%u required=%u errno=%d (%s)\n",
               name, param, required, errno, strerror(errno));
        return required ? -1 : 1;
    }
    report("VC4_LINUX_DRM_PARAM_OK name=%s id=%u value=0x%016llx\n",
           name, param, (unsigned long long)request.value);
    if (value_out != NULL) {
        *value_out = request.value;
    }
    return 0;
}

static int probe_vc4_uapi(int fd)
{
    static const struct {
        uint32_t id;
        const char *name;
        bool required;
    } params[] = {
        { VC4_PARAM_V3D_IDENT0, "V3D_IDENT0", true },
        { VC4_PARAM_V3D_IDENT1, "V3D_IDENT1", true },
        { VC4_PARAM_V3D_IDENT2, "V3D_IDENT2", true },
        { VC4_PARAM_SUPPORTS_BRANCHES, "SUPPORTS_BRANCHES", false },
        { VC4_PARAM_SUPPORTS_ETC1, "SUPPORTS_ETC1", false },
        { VC4_PARAM_SUPPORTS_THREADED_FS, "SUPPORTS_THREADED_FS", false },
        { VC4_PARAM_SUPPORTS_FIXED_RCL_ORDER,
          "SUPPORTS_FIXED_RCL_ORDER", false },
        { VC4_PARAM_SUPPORTS_MADVISE, "SUPPORTS_MADVISE", false },
        { VC4_PARAM_SUPPORTS_PERFMON, "SUPPORTS_PERFMON", false },
    };
    struct vc4_drm_create_bo create = {
        .size = VC4_PROBE_BO_SIZE,
    };
    struct vc4_drm_mmap_bo map_request;
    volatile uint32_t *words;
    uint64_t ident0 = 0;
    void *mapping;

    marker("VC4_LINUX_DRM_UAPI_START\n");
    for (unsigned index = 0;
         index < sizeof(params) / sizeof(params[0]); index++) {
        uint64_t *output = params[index].id == VC4_PARAM_V3D_IDENT0 ?
                           &ident0 : NULL;

        if (vc4_get_param(fd, params[index].id, params[index].name,
                          params[index].required, output) < 0) {
            marker("VC4_LINUX_DRM_UAPI_FAILED stage=get-param\n");
            return -1;
        }
    }
    if (ident0 != VC4_EXPECTED_IDENT0) {
        report("VC4_LINUX_DRM_IDENT_MISMATCH actual=0x%016llx expected=0x%016llx\n",
               (unsigned long long)ident0,
               (unsigned long long)VC4_EXPECTED_IDENT0);
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=identity\n");
        return -1;
    }
    marker("VC4_LINUX_DRM_IDENT_OK\n");

    if (ioctl(fd, VC4_DRM_IOCTL_CREATE_BO, &create) < 0 ||
        create.handle == 0) {
        report("VC4_LINUX_DRM_CREATE_BO_FAILED handle=%u errno=%d (%s)\n",
               create.handle, errno, strerror(errno));
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=create-bo\n");
        return -1;
    }

    memset(&map_request, 0, sizeof(map_request));
    map_request.handle = create.handle;
    if (ioctl(fd, VC4_DRM_IOCTL_MMAP_BO, &map_request) < 0 ||
        map_request.offset == 0) {
        report("VC4_LINUX_DRM_MMAP_BO_FAILED handle=%u offset=0x%016llx errno=%d (%s)\n",
               create.handle, (unsigned long long)map_request.offset,
               errno, strerror(errno));
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=mmap-bo\n");
        return -1;
    }

    mapping = mmap(NULL, VC4_PROBE_BO_SIZE, PROT_READ | PROT_WRITE,
                   MAP_SHARED, fd, (off_t)map_request.offset);
    if (mapping == MAP_FAILED) {
        report("VC4_LINUX_DRM_MMAP_FAILED handle=%u offset=0x%016llx errno=%d (%s)\n",
               create.handle, (unsigned long long)map_request.offset,
               errno, strerror(errno));
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=mmap\n");
        return -1;
    }

    words = mapping;
    words[0] = UINT32_C(0x56433442); /* VC4B */
    words[VC4_PROBE_BO_SIZE / sizeof(*words) - 1] =
        UINT32_C(0x4f4f4b21); /* distinct tail sentinel */
    __sync_synchronize();
    if (words[0] != UINT32_C(0x56433442) ||
        words[VC4_PROBE_BO_SIZE / sizeof(*words) - 1] !=
        UINT32_C(0x4f4f4b21)) {
        report("VC4_LINUX_DRM_BO_VERIFY_FAILED first=0x%08x last=0x%08x\n",
               words[0],
               words[VC4_PROBE_BO_SIZE / sizeof(*words) - 1]);
        (void)munmap(mapping, VC4_PROBE_BO_SIZE);
        marker("VC4_LINUX_DRM_UAPI_FAILED stage=bo-verify\n");
        return -1;
    }

    report("VC4_LINUX_DRM_BO_OK handle=%u offset=0x%016llx size=%u first=0x%08x last=0x%08x\n",
           create.handle, (unsigned long long)map_request.offset,
           VC4_PROBE_BO_SIZE, words[0],
           words[VC4_PROBE_BO_SIZE / sizeof(*words) - 1]);
    (void)munmap(mapping, VC4_PROBE_BO_SIZE);
    marker("VC4_LINUX_DRM_UAPI_OK\n");
    return 0;
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

static void store_pixel(volatile uint8_t *address, unsigned bytes,
                        uint32_t pixel)
{
    for (unsigned index = 0; index < bytes; index++) {
        address[index] = pixel >> (index * 8);
    }
}

static int paint_framebuffer(int fd)
{
    struct fb_fix_screeninfo fix;
    struct fb_var_screeninfo var;
    volatile uint8_t *framebuffer;
    uint32_t colors[4];
    unsigned bytes_per_pixel;
    unsigned width;
    unsigned height;
    size_t map_size;

    memset(&fix, 0, sizeof(fix));
    memset(&var, 0, sizeof(var));
    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) < 0 ||
        ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0) {
        return -1;
    }

    bytes_per_pixel = (var.bits_per_pixel + 7) / 8;
    if (bytes_per_pixel == 0 || bytes_per_pixel > 4 ||
        fix.line_length == 0 || fix.smem_len == 0) {
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
    if (width < 2 || height < 2) {
        errno = EINVAL;
        return -1;
    }

    map_size = fix.smem_len;
    framebuffer = mmap(NULL, map_size, PROT_READ | PROT_WRITE,
                       MAP_SHARED, fd, 0);
    if (framebuffer == MAP_FAILED) {
        return -1;
    }

    colors[0] = make_pixel(&var, 255, 0, 0);
    colors[1] = make_pixel(&var, 0, 255, 0);
    colors[2] = make_pixel(&var, 0, 0, 255);
    colors[3] = make_pixel(&var, 255, 255, 255);

    for (unsigned y = 0; y < height; y++) {
        volatile uint8_t *row = framebuffer +
                                (size_t)y * fix.line_length;
        unsigned vertical = y >= height / 2;

        for (unsigned x = 0; x < width; x++) {
            unsigned horizontal = x >= width / 2;
            uint32_t color = colors[vertical * 2 + horizontal];

            store_pixel(row + (size_t)x * bytes_per_pixel,
                        bytes_per_pixel, color);
        }
    }

    (void)msync((void *)framebuffer, map_size, MS_SYNC);
    (void)ioctl(fd, FBIOPAN_DISPLAY, &var);
    report("VC4_LINUX_FB_OK xres=%u yres=%u bpp=%u pitch=%u bytes=%u id=%.*s\n",
           width, height, var.bits_per_pixel, fix.line_length,
           fix.smem_len, (int)sizeof(fix.id), fix.id);
    (void)munmap((void *)framebuffer, map_size);
    return 0;
}

static int probe_framebuffer(void)
{
    static const char *const candidates[] = {
        "/dev/fb0",
        "/dev/fb1",
    };
    int saved_errno = ENOENT;

    for (unsigned index = 0;
         index < sizeof(candidates) / sizeof(candidates[0]); index++) {
        int fd = wait_open(candidates[index], O_RDWR, 300);

        if (fd < 0) {
            saved_errno = errno;
            continue;
        }
        if (paint_framebuffer(fd) == 0) {
            close(fd);
            return 0;
        }
        saved_errno = errno;
        close(fd);
    }

    errno = saved_errno;
    report("VC4_LINUX_FB_MISSING errno=%d (%s)\n",
           errno, strerror(errno));
    return -1;
}

static void report_driver_topology(void)
{
    static const struct {
        const char *label;
        const char *path;
    } paths[] = {
        { "DRI_CLASS", "/sys/class/drm" },
        { "DRM_CARD0", "/sys/class/drm/card0" },
        { "DRM_RENDER128", "/sys/class/drm/renderD128" },
        { "VC4_DRIVER", "/sys/bus/platform/drivers/vc4" },
        { "VC4_V3D_DRIVER", "/sys/bus/platform/drivers/vc4_v3d" },
        { "V3D_DRIVER", "/sys/bus/platform/drivers/v3d" },
        { "FKMS_DRIVER", "/sys/bus/platform/drivers/vc4-fkms-v3d" },
        { "V3D_DEVICE", "/sys/bus/platform/devices/3fc00000.v3d" },
    };

    for (unsigned index = 0;
         index < sizeof(paths) / sizeof(paths[0]); index++) {
        report_path(paths[index].label, paths[index].path);
    }

    if (path_exists("/sys/firmware/devicetree/base/soc/v3d@7ec00000/status")) {
        report_text_file(
            "DT_V3D_STATUS",
            "/sys/firmware/devicetree/base/soc/v3d@7ec00000/status");
    } else {
        report_text_file(
            "DT_V3D_STATUS",
            "/proc/device-tree/soc/v3d@7ec00000/status");
    }
}

int main(void)
{
    int card_result;
    int render_result;
    int uapi_result = -1;
    int framebuffer_result;
    int render_fd = -1;

    prepare_pseudo_filesystems();
    marker("VC4_LINUX_INIT_OK\n");
    marker("VC4_LINUX_DRM_PROBE_START\n");

    report_driver_topology();
    card_result = query_drm_node("CARD0", "/dev/dri/card0", NULL);
    render_result = query_drm_node("RENDER128", "/dev/dri/renderD128",
                                   &render_fd);
    if (render_result == 0) {
        uapi_result = probe_vc4_uapi(render_fd);
    }
    if (render_fd >= 0) {
        close(render_fd);
    }
    framebuffer_result = probe_framebuffer();

    report("VC4_LINUX_DRM_PROBE_DONE card0=%d render128=%d uapi=%d framebuffer=%d\n",
           card_result, render_result, uapi_result, framebuffer_result);
    if (card_result == 0 && render_result == 0 && uapi_result == 0) {
        marker("VC4_LINUX_V3D_DRIVER_OK\n");
    } else {
        marker("VC4_LINUX_V3D_DRIVER_MISSING\n");
    }

    marker("VC4_LINUX_INIT_IDLE\n");
    for (;;) {
        pause();
    }
}
