#include <stddef.h>
#include <stdint.h>

#define PERIPHERAL_BASE UINT32_C(0x3f000000)
#define UART_BASE       (PERIPHERAL_BASE + UINT32_C(0x201000))
#define UART_DR         (UART_BASE + UINT32_C(0x00))
#define UART_FR         (UART_BASE + UINT32_C(0x18))
#define UART_IBRD       (UART_BASE + UINT32_C(0x24))
#define UART_FBRD       (UART_BASE + UINT32_C(0x28))
#define UART_LCRH       (UART_BASE + UINT32_C(0x2c))
#define UART_CR         (UART_BASE + UINT32_C(0x30))
#define UART_ICR        (UART_BASE + UINT32_C(0x44))
#define UART_FR_TXFF    (UINT32_C(1) << 5)

/*
 * The ARM mailbox register window begins at peripheral offset 0xb880.
 * The surrounding ARM control/semaphore/mailbox block begins at 0xb800,
 * which is also where QEMU maps the complete device; using that larger
 * block as the register base shifts every documented mailbox register by
 * -0x80 and causes reads of offsets 0x00/0x18 rather than 0x80/0x98.
 */
#define MBOX_BASE       (PERIPHERAL_BASE + UINT32_C(0x00b880))
#define MBOX_READ       (MBOX_BASE + UINT32_C(0x00))
#define MBOX_STATUS     (MBOX_BASE + UINT32_C(0x18))
#define MBOX_WRITE      (MBOX_BASE + UINT32_C(0x20))
#define MBOX_FULL       UINT32_C(0x80000000)
#define MBOX_EMPTY      UINT32_C(0x40000000)
#define MBOX_PROPERTY   UINT32_C(8)

#define TAG_SET_PHYSICAL_SIZE UINT32_C(0x00048003)
#define TAG_SET_VIRTUAL_SIZE  UINT32_C(0x00048004)
#define TAG_SET_DEPTH         UINT32_C(0x00048005)
#define TAG_SET_PIXEL_ORDER   UINT32_C(0x00048006)
#define TAG_ALLOCATE_BUFFER   UINT32_C(0x00040001)
#define TAG_GET_PITCH         UINT32_C(0x00040008)
#define TAG_END               UINT32_C(0)

#define STATUS_ADDRESS        UINT32_C(0x00001000)
#define STATUS_SIGNATURE      UINT64_C(0x5643345f46424f4b) /* VC4_FBOK */
#define FAILURE_SIGNATURE     UINT64_C(0x5643345f46424641) /* VC4_FBFA */

#define WIDTH                 UINT32_C(640)
#define HEIGHT                UINT32_C(480)
#define DEPTH                 UINT32_C(32)

static volatile uint32_t property_buffer[30]
    __attribute__((aligned(16)));

static inline volatile uint32_t *mmio(uintptr_t address)
{
    return (volatile uint32_t *)address;
}

static void barrier(void)
{
#ifdef __aarch64__
    __asm__ volatile("dsb sy" ::: "memory");
#else
    __sync_synchronize();
#endif
}

static void uart_init(void)
{
    *mmio(UART_CR) = 0;
    *mmio(UART_ICR) = UINT32_C(0x7ff);
    *mmio(UART_IBRD) = 1;
    *mmio(UART_FBRD) = 40;
    *mmio(UART_LCRH) = UINT32_C(3) << 5;
    *mmio(UART_CR) = UINT32_C(0x301);
}

static void uart_putc(char character)
{
    while ((*mmio(UART_FR) & UART_FR_TXFF) != 0) {
    }
    *mmio(UART_DR) = (uint8_t)character;
}

static void uart_puts(const char *text)
{
    while (*text != '\0') {
        if (*text == '\n') {
            uart_putc('\r');
        }
        uart_putc(*text++);
    }
}

static void record_status(uint64_t signature, uint32_t base,
                          uint32_t pitch, uint32_t response)
{
    volatile uint64_t *status = (volatile uint64_t *)(uintptr_t)STATUS_ADDRESS;

    status[0] = signature;
    status[1] = base;
    status[2] = ((uint64_t)WIDTH << 32) | HEIGHT;
    status[3] = ((uint64_t)pitch << 32) | response;
    barrier();
}

static void build_property_request(void)
{
    static const uint32_t request[30] = {
        30 * sizeof(uint32_t),
        0,
        TAG_SET_PHYSICAL_SIZE, 8, 8, WIDTH, HEIGHT,
        TAG_SET_VIRTUAL_SIZE, 8, 8, WIDTH, HEIGHT,
        TAG_SET_DEPTH, 4, 4, DEPTH,
        TAG_SET_PIXEL_ORDER, 4, 4, 1,
        TAG_ALLOCATE_BUFFER, 8, 8, 16, 0,
        TAG_GET_PITCH, 4, 4, 0,
        TAG_END,
    };

    for (unsigned index = 0; index < 30; index++) {
        property_buffer[index] = request[index];
    }
    barrier();
}

static int mailbox_property_call(void)
{
    uint32_t address = (uint32_t)(uintptr_t)property_buffer;
    uint32_t request = (address & ~UINT32_C(0xf)) | MBOX_PROPERTY;

    for (uint32_t timeout = 0; timeout < UINT32_C(0x04000000); timeout++) {
        if ((*mmio(MBOX_STATUS) & MBOX_FULL) == 0) {
            *mmio(MBOX_WRITE) = request;
            barrier();
            break;
        }
        if (timeout == UINT32_C(0x03ffffff)) {
            return -1;
        }
    }

    for (uint32_t timeout = 0; timeout < UINT32_C(0x08000000); timeout++) {
        if ((*mmio(MBOX_STATUS) & MBOX_EMPTY) != 0) {
            continue;
        }
        uint32_t response = *mmio(MBOX_READ);

        if (response == request) {
            barrier();
            return property_buffer[1] == UINT32_C(0x80000000) ? 0 : -2;
        }
    }
    return -3;
}

static void paint_quadrants(uint32_t base, uint32_t pitch)
{
    volatile uint8_t *framebuffer =
        (volatile uint8_t *)(uintptr_t)(base & UINT32_C(0x3fffffff));

    for (uint32_t y = 0; y < HEIGHT; y++) {
        volatile uint32_t *row =
            (volatile uint32_t *)(framebuffer + (uintptr_t)y * pitch);
        uint32_t vertical = y >= HEIGHT / 2;

        for (uint32_t x = 0; x < WIDTH; x++) {
            uint32_t horizontal = x >= WIDTH / 2;
            uint32_t quadrant = vertical * 2 + horizontal;
            uint32_t color;

            switch (quadrant) {
            case 0:
                color = UINT32_C(0x000000ff); /* red bytes: ff 00 00 */
                break;
            case 1:
                color = UINT32_C(0x0000ff00);
                break;
            case 2:
                color = UINT32_C(0x00ff0000);
                break;
            default:
                color = UINT32_C(0x00ffffff);
                break;
            }
            row[x] = color;
        }
    }
    barrier();
}

void vc4_framebuffer_main(void)
{
    uint32_t base;
    uint32_t pitch;
    int result;

    uart_init();
    uart_puts("VC4_BARE_START\n");
    build_property_request();
    result = mailbox_property_call();
    base = property_buffer[23];
    pitch = property_buffer[28];

    if (result != 0 || base == 0 || pitch < WIDTH * 4) {
        record_status(FAILURE_SIGNATURE, base, pitch,
                      property_buffer[1]);
        uart_puts("VC4_BARE_FB_FAILED\n");
        return;
    }

    paint_quadrants(base, pitch);
    record_status(STATUS_SIGNATURE, base, pitch, property_buffer[1]);
    uart_puts("VC4_BARE_FB_OK\n");
}