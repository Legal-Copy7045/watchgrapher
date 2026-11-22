#include "led.h"

#include <Arduino.h>

namespace led {

static int pin_ = -1;
static State cur_ = OFF;
static volatile bool busy_ = false;

static void show(State s) {
    uint8_t r = 0, g = 0, b = 0;
    switch (s) {
        case IDLE:
            if (busy_) r = g = b = 4;
            else b = 8;
            break;
        case GOOD: g = 10; break;
        case WEAK: r = 10; g = 5; break;
        case NOSIGNAL: r = 6; b = 8; break;
        case CLIP: r = 14; break;
        default: break;
    }
    neopixelWrite(pin_, r, g, b);
}

void set_busy(bool busy) {
    busy_ = busy;
    if (pin_ >= 0 && cur_ == IDLE) show(IDLE);
}

void begin(int pin) {
    pin_ = pin;
    cur_ = OFF;
    set(IDLE);
}

void set(State s) {
    if (pin_ < 0 || s == cur_) return;
    cur_ = s;
    // Kept dim: this sits next to a watch on a bench, not across a room.
    show(s);
}

}  // namespace led
