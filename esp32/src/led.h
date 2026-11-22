// Status on the board's RGB LED, so the device reads at a glance without a
// phone: blue = idle, dim white = idle while storage is being prepared (first
// start after installing), green = good reading, amber = rate only (no
// amplitude), purple = listening but no watch found, red = input clipping.
#pragma once

namespace led {
enum State { OFF, IDLE, GOOD, WEAK, NOSIGNAL, CLIP };
void begin(int pin);
void set(State s);
// While storage is being prepared the idle colour is a dim white, not blue.
void set_busy(bool busy);
}  // namespace led
