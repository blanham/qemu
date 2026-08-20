#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

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
        length -= written;
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
    char buffer[256];
    va_list arguments;
    int length;

    va_start(arguments, format);
    length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);

    if (length <= 0) {
        return;
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

    report("VC4_LINUX_FB_OK xres=%u yres=%u bpp=%u pitch=%u bytes=%u\n",
           width, height, var.bits_per_pixel, fix.line_length,
           fix.smem_len);
    (void)munmap((void *)framebuffer, map_size);
    return 0;
}

static int open_framebuffer(void)
{
    static const char *const candidates[] = {
        "/dev/fb0",
        "/dev/fb1",
    };
    struct timespec delay = {
        .tv_sec = 0,
        .tv_nsec = 100000000,
    };
    int last_errno = ENOENT;

    for (unsigned attempt = 0; attempt < 300; attempt++) {
        for (unsigned index = 0;
             index < sizeof(candidates) / sizeof(candidates[0]); index++) {
            int fd = open(candidates[index], O_RDWR | O_CLOEXEC);

            if (fd >= 0) {
                return fd;
            }
            last_errno = errno;
        }
        nanosleep(&delay, NULL);
    }

    errno = last_errno;
    return -1;
}

int main(void)
{
    int framebuffer;

    prepare_pseudo_filesystems();
    marker("VC4_LINUX_INIT_OK\n");

    framebuffer = open_framebuffer();
    if (framebuffer < 0) {
        report("VC4_LINUX_FB_MISSING errno=%d (%s)\n",
               errno, strerror(errno));
    } else {
        if (paint_framebuffer(framebuffer) < 0) {
            report("VC4_LINUX_FB_FAILED errno=%d (%s)\n",
                   errno, strerror(errno));
        }
        close(framebuffer);
    }

    marker("VC4_LINUX_INIT_IDLE\n");
    for (;;) {
        pause();
    }
}
