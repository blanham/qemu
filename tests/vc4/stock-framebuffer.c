/*
 * Stock Raspberry Pi firmware framebuffer witness
 *
 * This payload is entered by start.elf as a normal kernel8.img.  It requests
 * a 640x480x32 framebuffer through property-channel mailbox 8, paints four
 * deterministic quadrants, and leaves a machine-readable result at physical
 * address 0x1000 for the host probe.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

typedef unsigned char uint8_t;
typedef unsigned int uint32_t;
typedef unsigned long uint64_t;
typedef unsigned long uintptr_t;

#define MBOX_BASE           UINT64_C(0x3f00b880)
#define MBOX_READ           0x00
#define MBOX_STATUS         0x18
#define MBOX_WRITE          0x20
#define MBOX_FULL           UINT32_C(0x80000000)
#define MBOX_EMPTY          UINT32_C(0x40000000)
#define MBOX_PROPERTY       8
#define MBOX_BUS_ALIAS      UINT32_C(0x40000000)
#define MBOX_WAIT_LIMIT     UINT32_C(0x04000000)

#define TAG_SET_PHYSICAL    UINT32_C(0x00048003)
#define TAG_SET_VIRTUAL     UINT32_C(0x00048004)
#define TAG_SET_DEPTH       UINT32_C(0x00048005)
#define TAG_SET_PIXEL_ORDER UINT32_C(0x00048006)
#define TAG_ALLOCATE        UINT32_C(0x00040001)
#define TAG_GET_PITCH       UINT32_C(0x00040008)

#define RESULT_ADDRESS      UINT64_C(0x00001000)
#define RESULT_MAGIC        UINT64_C(0x5643345f46422121) /* VC4_FB!! */

#define WIDTH               640u
#define HEIGHT              480u
#define DEPTH               32u
#define PIXEL_ORDER_RGB     1u

#define COLOR_RED           UINT32_C(0x000000ff)
#define COLOR_GREEN         UINT32_C(0x0000ff00)
#define COLOR_BLUE          UINT32_C(0x00ff0000)
#define COLOR_WHITE         UINT32_C(0x00ffffff)

#define UINT32_C(value) value##U
#define UINT64_C(value) value##UL

struct framebuffer_result {
    uint64_t magic;
    uint32_t status;
    uint32_t stage;
    uint64_t dtb;
    uint64_t mpidr;
    uint32_t property_response;
    uint32_t framebuffer_bus;
    uint32_t framebuffer_phys;
    uint32_t framebuffer_size;
    uint32_t pitch;
    uint32_t width;
    uint32_t height;
    uint32_t depth;
    uint32_t pixel_order;
    uint32_t samples[4];
};

static volatile uint32_t property_buffer[32]
    __attribute__((aligned(16)));

static volatile struct framebuffer_result *const result =
    (volatile struct framebuffer_result *)(uintptr_t)RESULT_ADDRESS;

static inline void data_sync(void)
{
    __asm__ volatile("dsb sy" ::: "memory");
}

static inline void event_send(void)
{
    __asm__ volatile("sev" ::: "memory");
}

static inline uint32_t mmio_read32(uintptr_t address)
{
    uint32_t value = *(volatile uint32_t *)address;

    __asm__ volatile("dmb sy" ::: "memory");
    return value;
}

static inline void mmio_write32(uintptr_t address, uint32_t value)
{
    __asm__ volatile("dmb sy" ::: "memory");
    *(volatile uint32_t *)address = value;
    __asm__ volatile("dmb sy" ::: "memory");
}

static uint32_t mailbox_call(volatile uint32_t *buffer)
{
    uint32_t physical = (uint32_t)(uintptr_t)buffer;
    uint32_t request = (physical | MBOX_BUS_ALIAS) | MBOX_PROPERTY;
    uint32_t remaining;

    data_sync();

    remaining = MBOX_WAIT_LIMIT;
    while (mmio_read32((uintptr_t)MBOX_BASE + MBOX_STATUS) & MBOX_FULL) {
        if (--remaining == 0) {
            return 2;
        }
    }

    mmio_write32((uintptr_t)MBOX_BASE + MBOX_WRITE, request);

    remaining = MBOX_WAIT_LIMIT;
    while (remaining-- != 0) {
        uint32_t response;

        if (mmio_read32((uintptr_t)MBOX_BASE + MBOX_STATUS) & MBOX_EMPTY) {
            continue;
        }
        response = mmio_read32((uintptr_t)MBOX_BASE + MBOX_READ);
        if (response == request) {
            data_sync();
            return buffer[1] == MBOX_FULL ? 0 : 4;
        }
    }

    return 3;
}

static void complete(uint32_t status)
{
    result->status = status;
    result->stage = status == 0 ? 6 : result->stage;
    data_sync();
    result->magic = RESULT_MAGIC;
    data_sync();
    event_send();
}

static uint32_t sample_pixel(volatile uint8_t *framebuffer,
                             uint32_t pitch, uint32_t x, uint32_t y)
{
    volatile uint32_t *pixel =
        (volatile uint32_t *)(framebuffer + (uint64_t)y * pitch + x * 4u);

    return *pixel;
}

void vc4_framebuffer_main(uint64_t dtb)
{
    uint32_t i = 2;
    uint32_t physical_index;
    uint32_t virtual_index;
    uint32_t depth_index;
    uint32_t pixel_order_index;
    uint32_t allocate_index;
    uint32_t pitch_index;
    uint32_t status;
    uint32_t fb_bus;
    uint32_t fb_phys;
    uint32_t fb_size;
    uint32_t pitch;
    uint32_t width;
    uint32_t height;
    uint32_t depth;
    uint32_t pixel_order;
    volatile uint8_t *framebuffer;
    uint64_t mpidr;
    uint32_t x;
    uint32_t y;

    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));

    result->magic = 0;
    result->status = 1;
    result->stage = 1;
    result->dtb = dtb;
    result->mpidr = mpidr;
    result->property_response = 0;
    result->framebuffer_bus = 0;
    result->framebuffer_phys = 0;
    result->framebuffer_size = 0;
    result->pitch = 0;
    result->width = 0;
    result->height = 0;
    result->depth = 0;
    result->pixel_order = 0;
    for (x = 0; x < 4; x++) {
        result->samples[x] = 0;
    }

    physical_index = i + 3;
    property_buffer[i++] = TAG_SET_PHYSICAL;
    property_buffer[i++] = 8;
    property_buffer[i++] = 8;
    property_buffer[i++] = WIDTH;
    property_buffer[i++] = HEIGHT;

    virtual_index = i + 3;
    property_buffer[i++] = TAG_SET_VIRTUAL;
    property_buffer[i++] = 8;
    property_buffer[i++] = 8;
    property_buffer[i++] = WIDTH;
    property_buffer[i++] = HEIGHT;

    depth_index = i + 3;
    property_buffer[i++] = TAG_SET_DEPTH;
    property_buffer[i++] = 4;
    property_buffer[i++] = 4;
    property_buffer[i++] = DEPTH;

    pixel_order_index = i + 3;
    property_buffer[i++] = TAG_SET_PIXEL_ORDER;
    property_buffer[i++] = 4;
    property_buffer[i++] = 4;
    property_buffer[i++] = PIXEL_ORDER_RGB;

    allocate_index = i + 3;
    property_buffer[i++] = TAG_ALLOCATE;
    property_buffer[i++] = 8;
    property_buffer[i++] = 8;
    property_buffer[i++] = 4096;
    property_buffer[i++] = 0;

    pitch_index = i + 3;
    property_buffer[i++] = TAG_GET_PITCH;
    property_buffer[i++] = 4;
    property_buffer[i++] = 0;
    property_buffer[i++] = 0;

    property_buffer[i++] = 0;
    property_buffer[0] = i * sizeof(uint32_t);
    property_buffer[1] = 0;

    result->stage = 2;
    status = mailbox_call(property_buffer);
    result->property_response = property_buffer[1];
    if (status != 0) {
        complete(status);
        return;
    }

    result->stage = 3;
    width = property_buffer[physical_index];
    height = property_buffer[physical_index + 1];
    if (property_buffer[virtual_index] < width ||
        property_buffer[virtual_index + 1] < height) {
        complete(5);
        return;
    }
    depth = property_buffer[depth_index];
    pixel_order = property_buffer[pixel_order_index];
    fb_bus = property_buffer[allocate_index];
    fb_size = property_buffer[allocate_index + 1];
    pitch = property_buffer[pitch_index];
    fb_phys = fb_bus & UINT32_C(0x3fffffff);

    result->framebuffer_bus = fb_bus;
    result->framebuffer_phys = fb_phys;
    result->framebuffer_size = fb_size;
    result->pitch = pitch;
    result->width = width;
    result->height = height;
    result->depth = depth;
    result->pixel_order = pixel_order;

    if (fb_phys == 0 || width != WIDTH || height != HEIGHT ||
        depth != DEPTH || pixel_order != PIXEL_ORDER_RGB) {
        complete(6);
        return;
    }
    if (pitch < width * 4u || fb_size < (uint64_t)pitch * height) {
        complete(7);
        return;
    }

    result->stage = 4;
    framebuffer = (volatile uint8_t *)(uintptr_t)fb_phys;
    for (y = 0; y < height; y++) {
        volatile uint32_t *row =
            (volatile uint32_t *)(framebuffer + (uint64_t)y * pitch);
        uint32_t top = y < height / 2u;

        for (x = 0; x < width; x++) {
            if (top) {
                row[x] = x < width / 2u ? COLOR_RED : COLOR_GREEN;
            } else {
                row[x] = x < width / 2u ? COLOR_BLUE : COLOR_WHITE;
            }
        }
    }
    data_sync();

    result->stage = 5;
    result->samples[0] = sample_pixel(framebuffer, pitch,
                                      width / 4u, height / 4u);
    result->samples[1] = sample_pixel(framebuffer, pitch,
                                      width * 3u / 4u, height / 4u);
    result->samples[2] = sample_pixel(framebuffer, pitch,
                                      width / 4u, height * 3u / 4u);
    result->samples[3] = sample_pixel(framebuffer, pitch,
                                      width * 3u / 4u, height * 3u / 4u);

    if (result->samples[0] != COLOR_RED ||
        result->samples[1] != COLOR_GREEN ||
        result->samples[2] != COLOR_BLUE ||
        result->samples[3] != COLOR_WHITE) {
        complete(8);
        return;
    }

    complete(0);
}
