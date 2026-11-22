#include "logx.h"

#include <cstdarg>

namespace logx {

static bool usb_on = false;

void begin(bool usb_host) {
    Serial.begin(115200);
#if ARDUINO_USB_MODE && !ARDUINO_USB_CDC_ON_BOOT
    if (!usb_host) {
        USBSerial.setTxTimeoutMs(0);   // never stall when nothing is listening
        USBSerial.begin();
        usb_on = true;
    }
#endif
}

bool usb_serial() { return usb_on; }

void printf(const char* fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n <= 0) return;
    size_t len = std::min<size_t>((size_t)n, sizeof buf - 1);
    Serial.write((const uint8_t*)buf, len);
#if ARDUINO_USB_MODE && !ARDUINO_USB_CDC_ON_BOOT
    if (usb_on && HWCDC::isConnected()) USBSerial.write((const uint8_t*)buf, len);
#endif
}

}  // namespace logx
