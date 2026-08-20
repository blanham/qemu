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

#define V3D_BASE        (PERIPHERAL_BASE + UINT32_C(0x00c00000))
#define V3D_IDENT0      (V3D_BASE + UINT32_C(0x000))
#define V3D_L2CACTL     (V3D_BASE + UINT32_C(0x020))
#define V3D_SLCACTL     (V3D_BASE + UINT32_C(0x024))
#define V3D_INTCTL      (V3D_BASE + UINT32_C(0x030))
#define V3D_INTENA      (V3D_BASE + UINT32_C(0x034))
#define V3D_INTDIS      (V3D_BASE + UINT32_C(0x038))
#define V3D_CT1CS       (V3D_BASE + UINT32_C(0x104))
#define V3D_CT1EA       (V3D_BASE + UINT32_C(0x10c))
#define V3D_CT1CA       (V3D_BASE + UINT32_C(0x114))
#define V3D_RFC         (V3D_BASE + UINT32_C(0x138))
#define V3D_ERRSTAT     (V3D_BASE + UINT32_C(0xf20))

#define V3D_EXPECTED_IDENT0 UINT32_C(0x02443356)
#define V3D_L2CCLR          (UINT32_C(1) << 2)
#define V3D_L2CENA          (UINT32_C(1) << 0)
#define V3D_INT_FRDONE      (UINT32_C(1) << 0)
#define V3D_CTRSTA          (UINT32_C(1) << 15)
#define V3D_CTRUN           (UINT32_C(1) << 5)
#define V3D_CTERR           (UINT32_C(1) << 3)

#define VC4_PACKET_HALT                         UINT8_C(0)
#define VC4_PACKET_STORE_MS_TILE_BUFFER         UINT8_C(24)
#define VC4_PACKET_STORE_MS_TILE_BUFFER_EOF     UINT8_C(25)
#define VC4_PACKET_STORE_TILE_BUFFER_GENERAL    UINT8_C(28)
#define VC4_PACKET_TILE_RENDERING_MODE_CONFIG   UINT8_C(113)
#define VC4_PACKET_CLEAR_COLORS                 UINT8_C(114)
#define VC4_PACKET_TILE_COORDINATES             UINT8_C(115)
#define VC4_RENDER_CONFIG_FORMAT_RGBA8888        UINT16_C(4)

#define STATUS_ADDRESS       UINT32_C(0x00001000)
#define STATUS_SIGNATURE     UINT64_C(0x5643345f5633444f) /* VC4_V3DO */
#define FAILURE_SIGNATURE    UINT64_C(0x5643345f56334446) /* VC4_V3DF */

#define WIDTH                 UINT32_C(512)
#define HEIGHT                UINT32_C(512)
#define DEPTH                 UINT32_C(32)
#define TILE_SIZE             UINT32_C(64)
#define FIRST_ACCEL_TILE      UINT8_C(2)
#define LAST_ACCEL_TILE       UINT8_C(5)
#define BACKGROUND_COLOR      UINT32_C(0x00ff0000) /* blue */
#define ACCELERATED_COLOR     UINT32_C(0x0000ffff) /* yellow */
#define RCL_CAPACITY          UINT32_C(512)

static volatile uint32_t property_buffer[30]
    __attribute__((aligned(16)));
static uint8_t render_control_list[RCL_CAPACITY]
    __attribute__((aligned(16)));

static inline volatile uint32_t *mmio(uintptr_t address)
{
    return (volatile uint32_t *)address;
}

static void barrier(void)
{
    __asm__ volatile("dsb sy" ::: "memory");
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
                          uint32_t pitch, uint32_t property_response,
                          uint32_t ident0, uint32_t rfc,
                          uint32_t intctl, uint32_t ct1cs,
                          uint32_t border_sample,
                          uint32_t center_sample)
{
    volatile uint64_t *status =
        (volatile uint64_t *)(uintptr_t)STATUS_ADDRESS;

    status[0] = signature;
    status[1] = base;
    status[2] = ((uint64_t)WIDTH << 32) | HEIGHT;
    status[3] = ((uint64_t)pitch << 32) | property_response;
    status[4] = ((uint64_t)ident0 << 32) | rfc;
    status[5] = ((uint64_t)intctl << 32) | ct1cs;
    status[6] = border_sample;
    status[7] = center_sample;
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
        if (*mmio(MBOX_READ) == request) {
            barrier();
            return property_buffer[1] == UINT32_C(0x80000000) ? 0 : -2;
        }
    }
    return -3;
}

static void paint_background(uint32_t physical_base, uint32_t pitch)
{
    volatile uint8_t *framebuffer =
        (volatile uint8_t *)(uintptr_t)physical_base;

    for (uint32_t y = 0; y < HEIGHT; y++) {
        volatile uint32_t *row =
            (volatile uint32_t *)(framebuffer + (uintptr_t)y * pitch);

        for (uint32_t x = 0; x < WIDTH; x++) {
            row[x] = BACKGROUND_COLOR;
        }
    }
    barrier();
}

static void emit_u8(uint32_t *offset, uint8_t value)
{
    if (*offset < RCL_CAPACITY) {
        render_control_list[(*offset)++] = value;
    }
}

static void emit_u16(uint32_t *offset, uint16_t value)
{
    emit_u8(offset, value);
    emit_u8(offset, value >> 8);
}

static void emit_u32(uint32_t *offset, uint32_t value)
{
    emit_u16(offset, value);
    emit_u16(offset, value >> 16);
}

static uint32_t build_clear_rcl(uint32_t framebuffer_bus_address)
{
    uint32_t offset = 0;

    /*
     * This follows the kernel driver's clear-only RCL sequence: publish the
     * clear values, perform a no-write store to seed/clear the tile buffer,
     * configure the render target, then store only the central 4x4 tiles.
     */
    emit_u8(&offset, VC4_PACKET_CLEAR_COLORS);
    emit_u32(&offset, ACCELERATED_COLOR);
    emit_u32(&offset, ACCELERATED_COLOR);
    emit_u32(&offset, UINT32_C(0x00ffffff));
    emit_u8(&offset, 0);

    emit_u8(&offset, VC4_PACKET_TILE_COORDINATES);
    emit_u8(&offset, 0);
    emit_u8(&offset, 0);
    emit_u8(&offset, VC4_PACKET_STORE_TILE_BUFFER_GENERAL);
    emit_u16(&offset, 0);
    emit_u32(&offset, 0);

    emit_u8(&offset, VC4_PACKET_TILE_RENDERING_MODE_CONFIG);
    emit_u32(&offset, framebuffer_bus_address);
    emit_u16(&offset, WIDTH);
    emit_u16(&offset, HEIGHT);
    emit_u16(&offset, VC4_RENDER_CONFIG_FORMAT_RGBA8888);

    for (uint8_t y = FIRST_ACCEL_TILE; y <= LAST_ACCEL_TILE; y++) {
        for (uint8_t x = FIRST_ACCEL_TILE; x <= LAST_ACCEL_TILE; x++) {
            bool last = x == LAST_ACCEL_TILE && y == LAST_ACCEL_TILE;

            emit_u8(&offset, VC4_PACKET_TILE_COORDINATES);
            emit_u8(&offset, x);
            emit_u8(&offset, y);
            emit_u8(&offset, last ?
                     VC4_PACKET_STORE_MS_TILE_BUFFER_EOF :
                     VC4_PACKET_STORE_MS_TILE_BUFFER);
        }
    }
    emit_u8(&offset, VC4_PACKET_HALT);

    return offset;
}

static int run_v3d_clear(uint32_t framebuffer_bus_address,
                         uint32_t *ident0_out,
                         uint32_t *rfc_out,
                         uint32_t *intctl_out,
                         uint32_t *ct1cs_out)
{
    uint32_t ident0 = *mmio(V3D_IDENT0);
    uint32_t rfc_before = *mmio(V3D_RFC);
    uint32_t rcl_size;
    uint32_t ct1cs = 0;

    *ident0_out = ident0;
    if (ident0 != V3D_EXPECTED_IDENT0) {
        return -1;
    }

    *mmio(V3D_INTDIS) = UINT32_C(0xf);
    *mmio(V3D_INTCTL) = UINT32_C(0xf);
    *mmio(V3D_CT1CS) = V3D_CTRSTA;
    *mmio(V3D_ERRSTAT) = UINT32_MAX;
    *mmio(V3D_L2CACTL) = V3D_L2CCLR | V3D_L2CENA;
    *mmio(V3D_SLCACTL) = UINT32_C(0x0f0f0f0f);

    rcl_size = build_clear_rcl(framebuffer_bus_address);
    if (rcl_size == 0 || rcl_size > RCL_CAPACITY) {
        return -2;
    }
    barrier();

    *mmio(V3D_INTENA) = V3D_INT_FRDONE;
    *mmio(V3D_CT1CA) = (uint32_t)(uintptr_t)render_control_list;
    *mmio(V3D_CT1EA) =
        (uint32_t)(uintptr_t)render_control_list + rcl_size;
    barrier();

    for (uint32_t timeout = 0; timeout < UINT32_C(0x10000000); timeout++) {
        ct1cs = *mmio(V3D_CT1CS);
        if ((ct1cs & V3D_CTRUN) == 0) {
            break;
        }
        if (timeout == UINT32_C(0x0fffffff)) {
            return -3;
        }
    }

    *rfc_out = *mmio(V3D_RFC);
    *intctl_out = *mmio(V3D_INTCTL);
    *ct1cs_out = ct1cs;

    if ((ct1cs & V3D_CTERR) != 0 || *mmio(V3D_ERRSTAT) != 0) {
        return -4;
    }
    if (*rfc_out != rfc_before + 1) {
        return -5;
    }
    if ((*intctl_out & V3D_INT_FRDONE) == 0) {
        return -6;
    }

    *mmio(V3D_INTCTL) = V3D_INT_FRDONE;
    barrier();
    return 0;
}

void vc4_framebuffer_main(void)
{
    uint32_t framebuffer_bus_address;
    uint32_t framebuffer_physical_address;
    uint32_t pitch;
    uint32_t ident0 = 0;
    uint32_t rfc = 0;
    uint32_t intctl = 0;
    uint32_t ct1cs = 0;
    uint32_t border_sample = 0;
    uint32_t center_sample = 0;
    int result;

    uart_init();
    uart_puts("VC4_BARE_V3D_START\n");

    build_property_request();
    result = mailbox_property_call();
    framebuffer_bus_address = property_buffer[23];
    framebuffer_physical_address =
        framebuffer_bus_address & UINT32_C(0x3fffffff);
    pitch = property_buffer[28];

    if (result == 0 && framebuffer_bus_address != 0 &&
        pitch >= WIDTH * sizeof(uint32_t)) {
        paint_background(framebuffer_physical_address, pitch);
        result = run_v3d_clear(framebuffer_bus_address, &ident0, &rfc,
                               &intctl, &ct1cs);
        border_sample = *(volatile uint32_t *)(uintptr_t)
            (framebuffer_physical_address + 16 * pitch + 16 * 4);
        center_sample = *(volatile uint32_t *)(uintptr_t)
            (framebuffer_physical_address +
             (HEIGHT / 2) * pitch + (WIDTH / 2) * 4);
    }

    if (result != 0 || border_sample != BACKGROUND_COLOR ||
        center_sample != ACCELERATED_COLOR) {
        record_status(FAILURE_SIGNATURE, framebuffer_bus_address, pitch,
                      property_buffer[1], ident0, rfc, intctl, ct1cs,
                      border_sample, center_sample);
        uart_puts("VC4_BARE_V3D_FAILED\n");
        return;
    }

    record_status(STATUS_SIGNATURE, framebuffer_bus_address, pitch,
                  property_buffer[1], ident0, rfc, intctl, ct1cs,
                  border_sample, center_sample);
    uart_puts("VC4_BARE_V3D_OK\n");
}
