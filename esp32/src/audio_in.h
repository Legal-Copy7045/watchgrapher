// Audio capture: an I2S microphone / ADC, a USB microphone, or the simulated
// watch, feeding a 16-bit ring buffer in PSRAM. I2S and the simulator run on
// their own task on core 0; a USB mic is fed from the USB client task.
#pragma once

#include <Arduino.h>

#include "settings.h"

namespace audio {

// Allocates the ring (`seconds` long) and starts the capture task. Returns
// false if the ring could not be allocated or the I2S driver failed.
bool begin(float seconds);

void set_capturing(bool on);
bool capturing();

// Total frames written since boot; the newest sample is frame total()-1.
uint64_t total();
// First frame of the current unbroken stream. The analysis never reaches back
// past it: audio from before a pause, a source switch or a rate change must
// not be analysed together with what follows.
uint64_t epoch();
// Current stream rate. Normally the configured rate; a USB mic that only runs
// slower (e.g. 44.1 kHz) lowers it.
int sample_rate();
float ring_seconds();

// Copy `n` samples ending at frame `end` (exclusive) as floats into `dst`.
// Returns the number copied (fewer if the ring does not hold them yet).
size_t copy_window(float* dst, size_t n, uint64_t end);

// Live level for the meter, in dBFS after gain; decays.
float level_db();
float gain_db();
// Clipped samples in roughly the last 2 s (clipping guard).
uint32_t clips_recent();
// Problems with the input, or what a USB mic is doing; empty if nothing to say.
String status();
// False while there is no audio to expect (no USB mic, I2S failed).
bool input_ready();

// Apply changed gain / AGC / simulator settings live.
void apply_settings();

}  // namespace audio
