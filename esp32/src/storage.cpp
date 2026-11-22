#include "storage.h"

#include <Arduino.h>
#include <LittleFS.h>

#include <atomic>

#include "led.h"
#include "logx.h"

namespace storage {

static std::atomic<bool> ready_{false};
static void (*after_)() = nullptr;

static void task(void*) {
    led::set_busy(true);
    uint32_t t0 = millis();
    bool ok = LittleFS.begin(false);
    if (!ok) {
        logx::printf("Preparing storage (first start after installing: a few minutes)...\n");
        ok = LittleFS.begin(true);
    }
    if (ok) {
        if (after_) after_();
        ready_ = true;
        logx::printf("Storage ready (%lu ms)\n", (unsigned long)(millis() - t0));
    } else {
        logx::printf("Storage could not be mounted -- watches and logs are unavailable\n");
    }
    led::set_busy(false);
    vTaskDelete(nullptr);
}

void begin(void (*after)()) {
    after_ = after;
    xTaskCreatePinnedToCore(task, "storage", 6144, nullptr, 1, nullptr, 0);
}

bool ready() { return ready_; }

}  // namespace storage
