/*
 * Tiny freestanding AArch64 /init for the VC4 Linux bring-up image.
 *
 * It proves userspace over the serial console, opens Linux /dev/fb0, discovers
 * the negotiated pixel layout, mmaps the framebuffer, and paints deterministic
 * red/green/blue/white quadrants for the host-side screendump verifier.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long uint64_t;
typedef unsigned long uintptr_t;
typedef long intptr_t;

#define SYS_IOCTL       29
#define SYS_MOUNT       40
#define SYS_OPENAT      56
#define SYS_CLOSE       57
#define SYS_WRITE       64
#define SYS_NANOSLEEP   101
#define SYS_MUNMAP      215
#define SYS_MMAP        222

#define AT_FDCWD        (-100)
#define O_RDWR          2
#define PROT_READ       1
#define PROT_WRITE      2
#define MAP_SHARED      1

#define FBIOGET_VSCREENINFO 0x4600
#define FBIOGET_FSCREENINFO 0x4602
#define FBIOPAN_DISPLAY     0x4606

#define OPEN_RETRIES    2000

struct timespec {
    long seconds;
    long nanoseconds;
};

struct fb_bitfield {
    uint32_t offset;
    uint32_t length;
    uint32_t msb_right;
};

struct fb_fix_screeninfo {
    char id[16];
    unsigned long smem_start;
    uint32_t smem_len;
    uint32_t type;
    uint32_t type_aux;
    uint32_t visual;
    uint16_t xpanstep;
    uint16_t ypanstep;
    uint16_t ywrapstep;
    uint32_t line_length;
    unsigned long mmio_start;
    uint32_t mmio_len;
    uint32_t accel;
    uint16_t capabilities;
    uint16_t reserved[2];
};

struct fb_var_screeninfo {
    uint32_t xres;
    uint32_t yres;
    uint32_t xres_virtual;
    uint32_t yres_virtual;
    uint32_t xoffset;
    uint32_t yoffset;
    uint32_t bits_per_pixel;
    uint32_t grayscale;
    struct fb_bitfield red;
    struct fb_bitfield green;
    struct fb_bitfield blue;
    struct fb_bitfield transp;
    uint32_t nonstd;
    uint32_t activate;
    uint32_t height;
    uint32_t width;
    uint32_t accel_flags;
    uint32_t pixclock;
    uint32_t left_margin;
    uint32_t right_margin;
    uint32_t upper_margin;
    uint32_t lower_margin;
    uint32_t hsync_len;
    uint32_t vsync_len;
    uint32_t sync;
    uint32_t vmode;
    uint32_t rotate;
    uint32_t colorspace;
    uint32_t reserved[4];
};

static long system_call6(long number, long argument0, long argument1,
                         long argument2, long argument3, long argument4,
                         long argument5)
{
    register long x0 __asm__("x0") = argument0;
    register long x1 __asm__("x1") = argument1;
    register long x2 __asm__("x2") = argument2;
    register long x3 __asm__("x3") = argument3;
    register long x4 __asm__("x4") = argument4;
    register long x5 __asm__("x5") = argument5;
    register long x8 __asm__("x8") = number;

    __asm__ volatile(
        "svc #0"
        : "+r"(x0)
        : "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5), "r"(x8)
        : "memory", "cc"
    );
    return x0;
}

static long system_call4(long number, long argument0, long argument1,
                         long argument2, long argument3)
{
    return system_call6(number, argument0, argument1, argument2, argument3,
                        0, 0);
}

static long system_call3(long number, long argument0, long argument1,
                         long argument2)
{
    return system_call6(number, argument0, argument1, argument2, 0, 0, 0);
}

static long system_call2(long number, long argument0, long argument1)
{
    return system_call6(number, argument0, argument1, 0, 0, 0, 0);
}

static long system_call1(long number, long argument0)
{
    return system_call6(number, argument0, 0, 0, 0, 0, 0);
}

static int syscall_failed(long value)
{
    return (unsigned long)value >= (unsigned long)-4095;
}

static void console_write(const char *message, unsigned long length)
{
    (void)system_call3(SYS_WRITE, 1, (long)message, length);
}

#define CONSOLE_LITERAL(text) console_write((text), sizeof(text) - 1)

static uint32_t scale_component(uint32_t value,
                                const struct fb_bitfield *field)
{
    uint32_t maximum;
    uint32_t scaled;

    if (field->length == 0) {
        return 0;
    }
    if (field->length >= 32) {
        return value;
    }
    maximum = (1u << field->length) - 1u;
    scaled = (value * maximum + 127u) / 255u;
    return scaled << field->offset;
}

static uint32_t pixel_value(const struct fb_var_screeninfo *variable,
                            uint32_t red, uint32_t green, uint32_t blue)
{
    uint32_t value = 0;

    value |= scale_component(red, &variable->red);
    value |= scale_component(green, &variable->green);
    value |= scale_component(blue, &variable->blue);
    if (variable->transp.length != 0) {
        value |= scale_component(255u, &variable->transp);
    }
    return value;
}

static void store_pixel(volatile uint8_t *address, uint32_t value,
                        uint32_t bytes_per_pixel)
{
    switch (bytes_per_pixel) {
    case 2:
        *(volatile uint16_t *)address = (uint16_t)value;
        break;
    case 3:
        address[0] = (uint8_t)value;
        address[1] = (uint8_t)(value >> 8);
        address[2] = (uint8_t)(value >> 16);
        break;
    case 4:
        *(volatile uint32_t *)address = value;
        break;
    default:
        break;
    }
}

static int open_framebuffer(void)
{
    static const char path[] = "/dev/fb0";
    static const struct timespec delay = { 0, 10 * 1000 * 1000 };
    int attempt;

    for (attempt = 0; attempt < OPEN_RETRIES; attempt++) {
        long descriptor = system_call4(SYS_OPENAT, AT_FDCWD,
                                       (long)path, O_RDWR, 0);
        if (!syscall_failed(descriptor)) {
            return (int)descriptor;
        }
        (void)system_call2(SYS_NANOSLEEP, (long)&delay, 0);
    }
    return -1;
}

static int paint_framebuffer(int descriptor)
{
    struct fb_fix_screeninfo fixed = { 0 };
    struct fb_var_screeninfo variable = { 0 };
    unsigned long mapping_size;
    long mapping;
    volatile uint8_t *framebuffer;
    uint32_t bytes_per_pixel;
    uint32_t colors[4];
    uint32_t x;
    uint32_t y;

    if (syscall_failed(system_call3(SYS_IOCTL, descriptor,
                                    FBIOGET_FSCREENINFO, (long)&fixed)) ||
        syscall_failed(system_call3(SYS_IOCTL, descriptor,
                                    FBIOGET_VSCREENINFO, (long)&variable))) {
        return 2;
    }

    bytes_per_pixel = (variable.bits_per_pixel + 7u) / 8u;
    if (variable.xres == 0 || variable.yres == 0 ||
        variable.xres_virtual < variable.xres ||
        variable.yres_virtual < variable.yres ||
        fixed.line_length < variable.xres * bytes_per_pixel ||
        (bytes_per_pixel != 2 && bytes_per_pixel != 3 &&
         bytes_per_pixel != 4)) {
        return 3;
    }

    mapping_size = (unsigned long)fixed.line_length * variable.yres_virtual;
    if (mapping_size == 0 ||
        (fixed.smem_len != 0 && mapping_size > fixed.smem_len)) {
        return 4;
    }

    mapping = system_call6(SYS_MMAP, 0, mapping_size,
                           PROT_READ | PROT_WRITE, MAP_SHARED,
                           descriptor, 0);
    if (syscall_failed(mapping)) {
        return 5;
    }
    framebuffer = (volatile uint8_t *)(uintptr_t)mapping;

    colors[0] = pixel_value(&variable, 255, 0, 0);
    colors[1] = pixel_value(&variable, 0, 255, 0);
    colors[2] = pixel_value(&variable, 0, 0, 255);
    colors[3] = pixel_value(&variable, 255, 255, 255);

    for (y = 0; y < variable.yres; y++) {
        volatile uint8_t *row = framebuffer +
            (unsigned long)(y + variable.yoffset) * fixed.line_length +
            (unsigned long)variable.xoffset * bytes_per_pixel;
        uint32_t vertical = y < variable.yres / 2u ? 0u : 2u;

        for (x = 0; x < variable.xres; x++) {
            uint32_t horizontal = x < variable.xres / 2u ? 0u : 1u;
            store_pixel(row + (unsigned long)x * bytes_per_pixel,
                        colors[vertical + horizontal], bytes_per_pixel);
        }
    }

    __asm__ volatile("dmb ishst" ::: "memory");
    (void)system_call3(SYS_IOCTL, descriptor, FBIOPAN_DISPLAY,
                       (long)&variable);
    (void)system_call2(SYS_MUNMAP, mapping, mapping_size);
    return 0;
}

void _start(void)
{
    static const char devtmpfs[] = "devtmpfs";
    static const char dev[] = "/dev";
    int descriptor;
    int result;

    CONSOLE_LITERAL("VC4_LINUX_INIT_START\n");

    /* Harmless when CONFIG_DEVTMPFS_MOUNT already mounted it. */
    (void)system_call6(SYS_MOUNT, (long)devtmpfs, (long)dev,
                       (long)devtmpfs, 0, 0, 0);

    descriptor = open_framebuffer();
    if (descriptor < 0) {
        CONSOLE_LITERAL("VC4_LINUX_FB_OPEN_FAIL\n");
        for (;;) {
            __asm__ volatile("yield");
        }
    }

    result = paint_framebuffer(descriptor);
    (void)system_call1(SYS_CLOSE, descriptor);
    if (result != 0) {
        switch (result) {
        case 2:
            CONSOLE_LITERAL("VC4_LINUX_FB_IOCTL_FAIL\n");
            break;
        case 3:
            CONSOLE_LITERAL("VC4_LINUX_FB_GEOMETRY_FAIL\n");
            break;
        case 4:
            CONSOLE_LITERAL("VC4_LINUX_FB_SIZE_FAIL\n");
            break;
        case 5:
            CONSOLE_LITERAL("VC4_LINUX_FB_MMAP_FAIL\n");
            break;
        default:
            CONSOLE_LITERAL("VC4_LINUX_FB_UNKNOWN_FAIL\n");
            break;
        }
    } else {
        CONSOLE_LITERAL("VC4_LINUX_FB_OK\n");
    }

    for (;;) {
        __asm__ volatile("yield");
    }
}
