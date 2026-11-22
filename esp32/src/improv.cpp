#include "improv.h"

#include <Arduino.h>
#include <WiFi.h>

#include "logx.h"
#include "net.h"
#include "settings.h"

namespace improv {

enum : uint8_t { T_STATE = 0x01, T_ERROR = 0x02, T_RPC = 0x03, T_RESULT = 0x04 };
enum : uint8_t { S_READY = 0x02, S_PROVISIONING = 0x03, S_PROVISIONED = 0x04 };
enum : uint8_t { E_NONE = 0x00, E_INVALID = 0x01, E_UNKNOWN_CMD = 0x02, E_NO_CONNECT = 0x03 };
enum : uint8_t { C_WIFI = 0x01, C_STATE = 0x02, C_INFO = 0x03, C_SCAN = 0x04 };

struct Port {
    Stream* s;
    uint8_t buf[256];
    size_t n = 0;
};

static void send(Stream* s, uint8_t type, const uint8_t* data, uint8_t len) {
    uint8_t hdr[9] = {'I', 'M', 'P', 'R', 'O', 'V', 1, type, len};
    uint8_t sum = 0;
    for (uint8_t b : hdr) sum += b;
    for (uint8_t i = 0; i < len; ++i) sum += data[i];
    s->write(hdr, 9);
    if (len) s->write(data, len);
    s->write(sum);
    s->write('\n');
    s->flush();
}

static void send_byte(Stream* s, uint8_t type, uint8_t v) { send(s, type, &v, 1); }

// RPC result: [command, total length, (len, string)...]
static void send_result(Stream* s, uint8_t cmd, std::initializer_list<String> strs) {
    uint8_t d[250];
    size_t k = 2;
    for (const String& x : strs) {
        size_t l = std::min<size_t>(x.length(), 64);
        if (k + 1 + l > sizeof d) break;
        d[k++] = (uint8_t)l;
        memcpy(d + k, x.c_str(), l);
        k += l;
    }
    d[0] = cmd;
    d[1] = (uint8_t)(k - 2);
    send(s, T_RESULT, d, (uint8_t)k);
}

static String url() { return "http://" + WiFi.localIP().toString() + "/"; }

static void handle_rpc(Stream* s, const uint8_t* d, uint8_t len) {
    if (len < 2 || d[1] != len - 2) { send_byte(s, T_ERROR, E_INVALID); return; }
    const uint8_t cmd = d[0];
    const uint8_t* p = d + 2;
    const uint8_t plen = d[1];
    switch (cmd) {
        case C_WIFI: {
            if (plen < 2 || p[0] + 2 > plen) { send_byte(s, T_ERROR, E_INVALID); return; }
            String ssid((const char*)p + 1, p[0]);
            uint8_t pl = p[1 + p[0]];
            if (2 + p[0] + pl > plen) { send_byte(s, T_ERROR, E_INVALID); return; }
            String pass((const char*)p + 2 + p[0], pl);
            send_byte(s, T_STATE, S_PROVISIONING);
            String u;
            if (!net::join(ssid, pass, 20000, u)) {
                send_byte(s, T_ERROR, E_NO_CONNECT);
                send_byte(s, T_STATE, S_READY);
                return;
            }
            send_byte(s, T_STATE, S_PROVISIONED);
            send_result(s, C_WIFI, {u});
            return;
        }
        case C_STATE:
            if (net::sta_connected()) {
                send_byte(s, T_STATE, S_PROVISIONED);
                send_result(s, C_STATE, {url()});
            } else {
                send_byte(s, T_STATE, S_READY);
            }
            return;
        case C_INFO: {
            String host;
            {
                SettingsLock lock;
                host = g_settings.hostname;
            }
            send_result(s, C_INFO, {"WatchGrapher", WG_FW_VERSION, "ESP32-S3", host});
            return;
        }
        case C_SCAN: {
            for (uint32_t t0 = millis(); !net::ready() && millis() - t0 < 60000;) delay(100);
            int n = WiFi.scanNetworks();
            for (int i = 0; i < n; ++i)
                send_result(s, C_SCAN, {WiFi.SSID(i), String(WiFi.RSSI(i)),
                                        WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? "NO" : "YES"});
            WiFi.scanDelete();
            send_result(s, C_SCAN, {});
            return;
        }
        default:
            send_byte(s, T_ERROR, E_UNKNOWN_CMD);
    }
}

// Byte-wise parser: resynchronises on the "IMPROV" header, so log output and
// line noise on the same port are ignored.
static void feed(Port& pt, uint8_t b) {
    static const char H[] = "IMPROV";
    if (pt.n < 6) {
        if (b == (uint8_t)H[pt.n]) pt.buf[pt.n++] = b;
        else pt.n = (b == 'I') ? (pt.buf[0] = b, 1) : 0;
        return;
    }
    pt.buf[pt.n++] = b;
    if (pt.n < 9) return;
    const uint8_t len = pt.buf[8];
    if (pt.n < (size_t)9 + len + 1) {
        if (pt.n >= sizeof pt.buf) pt.n = 0;
        return;
    }
    uint8_t sum = 0;
    for (size_t i = 0; i < (size_t)9 + len; ++i) sum += pt.buf[i];
    const bool ok = sum == pt.buf[9 + len] && pt.buf[6] == 1;
    const uint8_t type = pt.buf[7];
    pt.n = 0;
    if (!ok) { send_byte(pt.s, T_ERROR, E_INVALID); return; }
    if (type == T_RPC) handle_rpc(pt.s, pt.buf + 9, len);
}

static Port ports[2];
static int nports = 0;

static void task(void*) {
    for (;;) {
        for (int i = 0; i < nports; ++i)
            while (ports[i].s->available() > 0) feed(ports[i], (uint8_t)ports[i].s->read());
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void begin(bool usb_host) {
    (void)usb_host;   // logx::begin() decided whether the USB port is serial
    ports[nports++].s = &Serial;
#if ARDUINO_USB_MODE && !ARDUINO_USB_CDC_ON_BOOT
    if (logx::usb_serial()) ports[nports++].s = &USBSerial;
#endif
    xTaskCreatePinnedToCore(task, "improv", 4096, nullptr, 2, nullptr, 0);
}

}  // namespace improv
