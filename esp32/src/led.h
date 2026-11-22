// Status on the board's RGB LED, so the device reads at a glance without a
// phone: blue = idle, green = good reading, amber = rate only (no amplitude),
// purple = listening but no watch found, red = input clipping.
#pragma once

namespace led {
enum State { OFF, IDLE, GOOD, WEAK, NOSIGNAL, CLIP };
void begin(int pin);
void set(State s);
}  // namespace led
