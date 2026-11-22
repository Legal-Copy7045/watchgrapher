// Log lines go to the UART port and, when it is a serial port (not hosting a
// USB microphone), to the native USB port too -- so whichever port the board
// is plugged in by shows what it is doing.
#pragma once

#include <Arduino.h>

namespace logx {
void begin(bool usb_host);    // call first thing in setup()
bool usb_serial();            // the native USB port is a serial port
void printf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
}  // namespace logx
