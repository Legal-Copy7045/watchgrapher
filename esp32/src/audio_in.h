// Audio capture: an I2S microphone / ADC, or the simulated watch, feeding a
// 16-bit ring buffer in PSRAM. Runs as its own task on core 0.
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
const char* error();       // empty if fine

// Apply changed gain / AGC / simulator settings live.
void apply_settings();

}  // namespace audio
