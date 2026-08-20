/*
 * Diagnostic init for the VC4 heterogeneous Linux/framebuffer control.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#define _GNU_SOURCE

#include <dirent.h>
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
#include <sys/types.h>
#include <unistd.h>

static int console_fd = STDERR_FILENO;

static void report(const char *format, ...)
{
    char buffer[2048];
    va_list ap;
    int length;

    va_start(ap, format);
    length = vsnprintf(buffer, sizeof(buffer), format, ap);
    va_end(ap);
    if (length < 0) {
        return;
    }
    if ((size_t)length >= sizeof(buffer)) {
        length = sizeof(buffer) - 1;
    }
    (void)write(console_fd, buffer, length);
    if (console_fd != STDERR_FILENO) {
        (void)write(STDERR_FILENO, buffer, length);
    }
}

static void mount_one(const char *source, const char *target,
                      const char *filesystem, unsigned long flags,
                      const char *data)
{
    if (mkdir(target, 0755) < 0 && errno != EEXIST) {
        report("VC4_MKDIR_FAIL target=%s errno=%d (%s)\n",
               target, errno, strerror(errno));
        return;
    }
    if (mount(source, target, filesystem, flags, data) < 0 && errno != EBUSY) {
        report("VC4_MOUNT_FAIL fs=%s target=%s errno=%d (%s)\n",
               filesystem, target, errno, strerror(errno));
    }
}

static void dump_file(const char *path)
{
    char buffer[4096];
    int fd;
    ssize_t count;

    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        report("VC4_FILE_FAIL path=%s errno=%d (%s)\n",
               path, errno, strerror(errno));
        return;
    }
    report("VC4_FILE_BEGIN %s\n", path);
    while ((count = read(fd, buffer, sizeof(buffer))) > 0) {
        (void)write(console_fd, buffer, count);
    }
    if (count < 0) {
        report("VC4_FILE_READ_FAIL path=%s errno=%d (%s)\n",
               path, errno, strerror(errno));
    }
    report("\nVC4_FILE_END %s\n", path);
    close(fd);
}

static void list_directory(const char *path)
{
    DIR *directory;
    struct dirent *entry;

    directory = opendir(path);
    if (!directory) {
        report("VC4_DIR_FAIL path=%s errno=%d (%s)\n",
               path, errno, strerror(errno));
        return;
    }
    report("VC4_DIR_BEGIN %s\n", path);
    while ((entry = readdir(directory)) != NULL) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) {
            continue;
        }
        report("  %s\n", entry->d_name);
    }
    report("VC4_DIR_END %s\n", path);
    closedir(directory);
}

static uint32_t component(uint8_t value, struct fb_bitfield field)
{
    uint64_t maximum;
    uint64_t scaled;

    if (!field.length) {
        return 0;
    }
    maximum = field.length >= 32 ? UINT32_MAX :
              ((UINT64_C(1) << field.length) - 1);
    scaled = ((uint64_t)value * maximum + 127) / 255;
    return (uint32_t)(scaled << field.offset);
}

static uint32_t pixel_value(const struct fb_var_screeninfo *var,
                            uint8_t red, uint8_t green, uint8_t blue)
{
    return component(red, var->red) |
           component(green, var->green) |
           component(blue, var->blue) |
           component(255, var->transp);
}

static void store_pixel(uint8_t *destination, unsigned bytes,
                        uint32_t value)
{
    unsigned index;

    for (index = 0; index < bytes; index++) {
        destination[index] = value >> (index * 8);
    }
}

static int open_framebuffer(void)
{
    static const char *const paths[] = {
        "/dev/fb0",
        "/dev/graphics/fb0",
    };
    unsigned attempt;
    unsigned index;

    for (attempt = 0; attempt < 100; attempt++) {
        for (index = 0; index < sizeof(paths) / sizeof(paths[0]); index++) {
            int fd = open(paths[index], O_RDWR | O_CLOEXEC);

            if (fd >= 0) {
                report("VC4_LINUX_FB_DEVICE path=%s attempt=%u\n",
                       paths[index], attempt);
                return fd;
            }
        }
        if (!(attempt % 10)) {
            report("VC4_LINUX_FB_WAIT attempt=%u errno=%d (%s)\n",
                   attempt, errno, strerror(errno));
        }
        usleep(100000);
    }
    return -1;
}

static bool paint_framebuffer(int fd)
{
    struct fb_fix_screeninfo fix;
    struct fb_var_screeninfo var;
    uint8_t *memory;
    size_t mapping_size;
    unsigned bytes_per_pixel;
    uint32_t colours[4];
    unsigned x;
    unsigned y;

    if (ioctl(fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        report("VC4_LINUX_FB_FIX_FAIL errno=%d (%s)\n",
               errno, strerror(errno));
        return false;
    }
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0) {
        report("VC4_LINUX_FB_VAR_FAIL errno=%d (%s)\n",
               errno, strerror(errno));
        return false;
    }
    report(
        "VC4_LINUX_FB_INFO id=%.*s smem=%#lx len=%u line=%u "
        "x=%u y=%u xv=%u yv=%u bpp=%u "
        "r=%u/%u g=%u/%u b=%u/%u a=%u/%u\n",
        (int)sizeof(fix.id), fix.id, fix.smem_start, fix.smem_len,
        fix.line_length, var.xres, var.yres, var.xres_virtual,
        var.yres_virtual, var.bits_per_pixel,
        var.red.offset, var.red.length,
        var.green.offset, var.green.length,
        var.blue.offset, var.blue.length,
        var.transp.offset, var.transp.length);

    bytes_per_pixel = (var.bits_per_pixel + 7) / 8;
    if (bytes_per_pixel < 2 || bytes_per_pixel > 4 ||
        !var.xres || !var.yres || !fix.line_length) {
        report("VC4_LINUX_FB_UNSUPPORTED bytes=%u x=%u y=%u line=%u\n",
               bytes_per_pixel, var.xres, var.yres, fix.line_length);
        return false;
    }
    mapping_size = (size_t)fix.line_length * var.yres;
    if (fix.smem_len && mapping_size > fix.smem_len) {
        mapping_size = fix.smem_len;
    }
    memory = mmap(NULL, mapping_size, PROT_READ | PROT_WRITE,
                  MAP_SHARED, fd, 0);
    if (memory == MAP_FAILED) {
        report("VC4_LINUX_FB_MMAP_FAIL size=%zu errno=%d (%s)\n",
               mapping_size, errno, strerror(errno));
        return false;
    }

    colours[0] = pixel_value(&var, 255, 0, 0);
    colours[1] = pixel_value(&var, 0, 255, 0);
    colours[2] = pixel_value(&var, 0, 0, 255);
    colours[3] = pixel_value(&var, 255, 255, 255);
    report("VC4_LINUX_FB_COLOURS red=%#x green=%#x blue=%#x white=%#x\n",
           colours[0], colours[1], colours[2], colours[3]);

    for (y = 0; y < var.yres; y++) {
        for (x = 0; x < var.xres; x++) {
            unsigned quadrant = (y >= var.yres / 2) * 2 +
                                  (x >= var.xres / 2);
            size_t offset = (size_t)y * fix.line_length +
                            (size_t)x * bytes_per_pixel;

            if (offset + bytes_per_pixel > mapping_size) {
                continue;
            }
            store_pixel(memory + offset, bytes_per_pixel,
                        colours[quadrant]);
        }
    }
    if (msync(memory, mapping_size, MS_SYNC) < 0) {
        report("VC4_LINUX_FB_MSYNC_FAIL errno=%d (%s)\n",
               errno, strerror(errno));
    }
    (void)ioctl(fd, FBIOBLANK, FB_BLANK_UNBLANK);
    munmap(memory, mapping_size);
    sync();
    return true;
}

int main(void)
{
    int fd;

    mount_one("proc", "/proc", "proc", 0, NULL);
    mount_one("sysfs", "/sys", "sysfs", 0, NULL);
    mount_one("devtmpfs", "/dev", "devtmpfs", 0, "mode=0755");

    fd = open("/dev/console", O_WRONLY | O_NOCTTY | O_CLOEXEC);
    if (fd >= 0) {
        console_fd = fd;
    }
    report("VC4_LINUX_INIT_START pid=%ld\n", (long)getpid());
    dump_file("/proc/version");
    dump_file("/proc/cmdline");
    dump_file("/proc/fb");
    list_directory("/dev");
    list_directory("/sys/class/graphics");

    fd = open_framebuffer();
    if (fd < 0) {
        report("VC4_LINUX_FB_FAIL stage=open errno=%d (%s)\n",
               errno, strerror(errno));
    } else if (!paint_framebuffer(fd)) {
        report("VC4_LINUX_FB_FAIL stage=paint errno=%d (%s)\n",
               errno, strerror(errno));
        close(fd);
    } else {
        report("VC4_LINUX_FB_OK\n");
        close(fd);
    }

    for (;;) {
        sleep(60);
    }
}
