// Improv Wi-Fi over serial (https://www.improv-wifi.com/serial/): lets the
// browser installer ask for Wi-Fi details right after flashing, and gives it
// the device's address to open. Joining a network is optional -- the device's
// own hotspot always works.
#pragma once

namespace improv {
// Listen on the UART port, and on the native USB port unless `usb_host`
// (a USB microphone owns that port).
void begin(bool usb_host);
}
