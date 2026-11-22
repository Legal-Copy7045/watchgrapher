// USB Audio Class host for a microphone, on ESP-IDF 4.4's USB Host Library.
//
// Two tasks: the host-library daemon, and a client task that enumerates the
// mic, configures it, and services its isochronous IN transfers. Descriptor
// parsing and format choice are in lib/wguac (host-tested).
#include "usb_audio.h"

#include <Arduino.h>
#include <usb/usb_host.h>

#include <atomic>
#include <cstring>
#include <vector>

#include "uac.h"

namespace usbmic {

static constexpr int PACKETS = 8;      // 8 ms of audio per transfer at full speed
static constexpr int TRANSFERS = 4;    // in flight, so the stream never starves

static Sink sink_ = nullptr;
static uint32_t preferred_ = 48000, min_rate_ = 32000;
static std::atomic<bool> started_{false};
static usb_host_client_handle_t client = nullptr;

// Device state, owned by the client task.
static usb_device_handle_t dev = nullptr;
static std::atomic<uint8_t> new_addr{0};
static std::atomic<bool> gone{false};
static uac::AltFormat fmt;
static uac::Device desc;
static usb_transfer_t* xfers[TRANSFERS] = {};
static std::atomic<int> inflight{0};
static std::atomic<bool> streaming_{false};
static std::atomic<uint32_t> rate_{0};
static bool claimed = false;

static portMUX_TYPE stmux = portMUX_INITIALIZER_UNLOCKED;
static char status_[160] = "No USB microphone connected.";
static char product_[48] = "";

static void set_status(const char* s) {
    portENTER_CRITICAL(&stmux);
    strlcpy(status_, s, sizeof status_);
    portEXIT_CRITICAL(&stmux);
}

// ---- control transfers (synchronous, from the client task only) ------------

static usb_transfer_t* ctrl = nullptr;
static volatile bool ctrl_done = false;

static void ctrl_cb(usb_transfer_t* t) { ctrl_done = true; }

// Returns bytes of data received (IN) / sent (OUT), or -1 on failure.
static int control(uint8_t bmRequestType, uint8_t bRequest, uint16_t wValue, uint16_t wIndex,
                   uint16_t wLength, uint8_t* data) {
    if (!ctrl || wLength + 8 > (int)ctrl->data_buffer_size) return -1;
    uint8_t* b = ctrl->data_buffer;
    b[0] = bmRequestType; b[1] = bRequest;
    b[2] = wValue & 255;  b[3] = wValue >> 8;
    b[4] = wIndex & 255;  b[5] = wIndex >> 8;
    b[6] = wLength & 255; b[7] = wLength >> 8;
    if (!(bmRequestType & 0x80) && wLength && data) memcpy(b + 8, data, wLength);
    ctrl->num_bytes = 8 + wLength;
    ctrl->device_handle = dev;
    ctrl->bEndpointAddress = 0;
    ctrl->callback = ctrl_cb;
    ctrl->context = nullptr;
    ctrl_done = false;
    if (usb_host_transfer_submit_control(client, ctrl) != ESP_OK) return -1;
    uint32_t t0 = millis();
    while (!ctrl_done && millis() - t0 < 1000) usb_host_client_handle_events(client, pdMS_TO_TICKS(5));
    if (!ctrl_done || ctrl->status != USB_TRANSFER_STATUS_COMPLETED) return -1;
    int got = ctrl->actual_num_bytes - 8;
    if ((bmRequestType & 0x80) && data && got > 0) memcpy(data, b + 8, got);
    return got;
}

// ---- streaming ----------------------------------------------------------------

static void iso_cb(usb_transfer_t* t) {
    if (t->status == USB_TRANSFER_STATUS_NO_DEVICE || t->status == USB_TRANSFER_STATUS_CANCELED ||
        !streaming_) {
        inflight--;
        return;
    }
    float buf[512];
    const uint32_t r = rate_;
    for (int i = 0; i < t->num_isoc_packets; ++i) {
        const usb_isoc_packet_desc_t& p = t->isoc_packet_desc[i];
        if (p.status != USB_TRANSFER_STATUS_COMPLETED || p.actual_num_bytes <= 0) continue;
        size_t n = uac::to_float(t->data_buffer + i * fmt.mps, p.actual_num_bytes, fmt.channels,
                                 fmt.subframe, buf, 512);
        if (n && sink_) sink_(buf, n, r);
    }
    for (int i = 0; i < t->num_isoc_packets; ++i) t->isoc_packet_desc[i].num_bytes = fmt.mps;
    t->num_bytes = fmt.mps * t->num_isoc_packets;
    if (usb_host_transfer_submit(t) != ESP_OK) inflight--;
}

static void teardown() {
    streaming_ = false;
    // Wait for in-flight transfers to come back before freeing them.
    uint32_t t0 = millis();
    while (inflight > 0 && millis() - t0 < 500) usb_host_client_handle_events(client, pdMS_TO_TICKS(5));
    for (auto& x : xfers)
        if (x) { usb_host_transfer_free(x); x = nullptr; }
    if (claimed && dev) usb_host_interface_release(client, dev, fmt.iface);
    claimed = false;
    if (dev) usb_host_device_close(client, dev);
    dev = nullptr;
    rate_ = 0;
    inflight = 0;
}

static void fail(const char* why) {
    char s[160];
    snprintf(s, sizeof s, "%s%s%s", product_[0] ? product_ : "USB device",
             product_[0] ? ": " : ": ", why);
    set_status(s);
    teardown();
}

static void product_name(usb_device_handle_t d) {
    product_[0] = 0;
    usb_device_info_t info;
    if (usb_host_device_info(d, &info) != ESP_OK || !info.str_desc_product) return;
    const usb_str_desc_t* s = info.str_desc_product;
    int n = (s->bLength - 2) / 2, k = 0;
    for (int i = 0; i < n && k < (int)sizeof product_ - 1; ++i) {
        uint16_t c = s->wData[i];
        product_[k++] = (c >= 32 && c < 127) ? (char)c : '?';
    }
    product_[k] = 0;
}

static void open_device(uint8_t addr) {
    if (dev) return;   // one microphone at a time
    if (usb_host_device_open(client, addr, &dev) != ESP_OK) {
        dev = nullptr;
        set_status("A USB device was connected but could not be opened.");
        return;
    }
    product_name(dev);
    const usb_config_desc_t* cfg = nullptr;
    if (usb_host_get_active_config_descriptor(dev, &cfg) != ESP_OK || !cfg) return fail("no configuration.");
    if (!uac::parse_config((const uint8_t*)cfg, cfg->wTotalLength, desc))
        return fail("not a USB microphone (no audio input stream found).");

    std::vector<uac::Span> spans;
    if (desc.uac2 && desc.clock_id) {
        // RANGE of the clock's sampling-frequency control.
        uint8_t r[2 + 12 * 16];
        int got = control(0xA1, 0x02, 0x0100, (uint16_t)(desc.clock_id << 8 | desc.ac_iface),
                          sizeof r, r);
        if (got > 2) uac::parse_rate_ranges(r, got, spans);
    }
    uac::Choice ch;
    if (!uac::choose(desc, preferred_, min_rate_, spans, ch))
        return fail("no supported format (needs PCM at 32-48 kHz on a full-speed interface).");
    fmt = desc.alts[ch.alt_index];

    if (usb_host_interface_claim(client, dev, fmt.iface, fmt.alt) != ESP_OK)
        return fail("could not claim the audio interface.");
    claimed = true;
    control(0x01, 0x0B, fmt.alt, fmt.iface, 0, nullptr);   // SET_INTERFACE

    // Sampling rate.
    if (desc.uac2 && desc.clock_id) {
        uint8_t v[4] = {uint8_t(ch.rate), uint8_t(ch.rate >> 8), uint8_t(ch.rate >> 16), uint8_t(ch.rate >> 24)};
        control(0x21, 0x01, 0x0100, (uint16_t)(desc.clock_id << 8 | desc.ac_iface), 4, v);
    } else if (!desc.uac2 && (fmt.freq_control || fmt.rates.size() > 1 || fmt.continuous)) {
        uint8_t v[3] = {uint8_t(ch.rate), uint8_t(ch.rate >> 8), uint8_t(ch.rate >> 16)};
        control(0x22, 0x01, 0x0100, fmt.ep, 3, v);
    }
    // Unmute (master channel). Many mics ignore or stall this; that is fine.
    if (desc.has_ac && desc.feature_unit) {
        uint8_t z = 0;
        control(0x21, 0x01, 0x0100, (uint16_t)(desc.feature_unit << 8 | desc.ac_iface), 1, &z);
    }

    rate_ = ch.rate;
    streaming_ = true;
    for (auto& x : xfers) {
        if (usb_host_transfer_alloc(fmt.mps * PACKETS, PACKETS, &x) != ESP_OK) return fail("out of memory.");
        x->device_handle = dev;
        x->bEndpointAddress = fmt.ep;
        x->callback = iso_cb;
        x->context = nullptr;
        x->num_bytes = fmt.mps * PACKETS;
        for (int i = 0; i < PACKETS; ++i) x->isoc_packet_desc[i].num_bytes = fmt.mps;
        if (usb_host_transfer_submit(x) == ESP_OK) inflight++;
    }
    if (inflight == 0) return fail("the audio stream would not start.");

    char s[160];
    snprintf(s, sizeof s, "%s: %u Hz, %u-bit, %s%s", product_[0] ? product_ : "USB microphone",
             (unsigned)ch.rate, fmt.bits, fmt.channels == 1 ? "mono" : "first of 2+ channels",
             desc.uac2 ? " (UAC2)" : "");
    set_status(s);
}

static void client_cb(const usb_host_client_event_msg_t* msg, void*) {
    if (msg->event == USB_HOST_CLIENT_EVENT_NEW_DEV) new_addr = msg->new_dev.address;
    else if (msg->event == USB_HOST_CLIENT_EVENT_DEV_GONE && msg->dev_gone.dev_hdl == dev) gone = true;
}

static void client_task(void*) {
    usb_host_client_config_t cc = {};
    cc.is_synchronous = false;
    cc.max_num_event_msg = 5;
    cc.async.client_event_callback = client_cb;
    cc.async.callback_arg = nullptr;
    if (usb_host_client_register(&cc, &client) != ESP_OK) {
        set_status("USB host client failed to start.");
        vTaskDelete(nullptr);
    }
    usb_host_transfer_alloc(8 + 256, 0, &ctrl);
    for (;;) {
        usb_host_client_handle_events(client, pdMS_TO_TICKS(50));
        uint8_t a = new_addr.exchange(0);
        if (a) open_device(a);
        if (gone.exchange(false)) {
            teardown();
            set_status("USB microphone disconnected.");
        }
    }
}

static void host_task(void*) {
    for (;;) {
        uint32_t flags = 0;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
    }
}

bool begin(uint32_t preferred, uint32_t min_rate, Sink sink) {
    if (started_) return true;
    preferred_ = preferred;
    min_rate_ = min_rate;
    sink_ = sink;
    usb_host_config_t hc = {};
    hc.skip_phy_setup = false;
    hc.intr_flags = ESP_INTR_FLAG_LEVEL1;
    esp_err_t e = usb_host_install(&hc);
    if (e != ESP_OK) {
        char s[96];
        snprintf(s, sizeof s, "USB host failed to start: %s", esp_err_to_name(e));
        set_status(s);
        return false;
    }
    xTaskCreatePinnedToCore(host_task, "usbhost", 4096, nullptr, 6, nullptr, 0);
    xTaskCreatePinnedToCore(client_task, "usbmic", 6144, nullptr, 6, nullptr, 0);
    started_ = true;
    return true;
}

bool started() { return started_; }
bool streaming() { return streaming_; }
uint32_t rate() { return rate_; }

void status(char* buf, size_t len) {
    portENTER_CRITICAL(&stmux);
    strlcpy(buf, status_, len);
    portEXIT_CRITICAL(&stmux);
}

}  // namespace usbmic
