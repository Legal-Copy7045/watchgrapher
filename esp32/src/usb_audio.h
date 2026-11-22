// USB microphone input: the ESP32-S3's native USB port as a USB host, reading
// a USB Audio Class 1 or 2 microphone (most USB-C lavalier / desk mics).
//
// The S3's USB port is full speed, so the mic must offer a full-speed
// interface -- nearly all do. The port must also supply 5 V to the mic; see
// esp32/README.md ("USB-C microphone").
#pragma once

#include <cstddef>
#include <cstdint>

namespace usbmic {

// Receives each block of mono float samples and the stream's sample rate.
using Sink = void (*)(const float* x, size_t n, uint32_t rate);

// Install the USB host stack and start watching for a microphone. Takes over
// the native USB port (its serial/JTAG function stops until the next boot).
// `preferred` is the analysis rate; the mic may run slower, down to `min_rate`.
bool begin(uint32_t preferred, uint32_t min_rate, Sink sink);
bool started();

// Human-readable status for the UI: what is connected and how it streams,
// or why nothing is.
void status(char* buf, size_t len);
bool streaming();
uint32_t rate();

}  // namespace usbmic
