#include "storage.h"

#include <Arduino.h>
#include <LittleFS.h>

#include <atomic>
#include <sys/stat.h>

#include "led.h"
#include "logx.h"

namespace storage {

static std::atomic<bool> ready_{false};
static std::atomic<uint32_t> rev_{0};   // collection revision
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
    // A fresh revision each boot: a page left open across a restart reloads.
    rev_ = esp_random() & 0xFFFFFF;
    xTaskCreatePinnedToCore(task, "storage", 6144, nullptr, 1, nullptr, 0);
}

bool ready() { return ready_; }

static const char* COLL = "/collection.json";
static const char* COLL_TMP = "/collection.tmp";
static const char* COLL_BAK = "/collection.bak";

static SemaphoreHandle_t coll_mtx() {
    static SemaphoreHandle_t m = xSemaphoreCreateRecursiveMutex();
    return m;
}
CollLock::CollLock() { xSemaphoreTakeRecursive(coll_mtx(), portMAX_DELAY); }
CollLock::~CollLock() { xSemaphoreGiveRecursive(coll_mtx()); }

// stat() rather than exists(): a missing file is logged as an error by the
// VFS layer when opened.
static bool exists(const char* path) {
    struct stat st;
    return ::stat((String("/littlefs") + path).c_str(), &st) == 0;
}

bool coll_open(File& f) {
    if (!ready_) return false;
    const char* p = exists(COLL) ? COLL : (exists(COLL_BAK) ? COLL_BAK : nullptr);
    if (!p) return false;
    f = LittleFS.open(p, "r");
    return (bool)f;
}

bool coll_replace(const std::function<bool(File&)>& write) {
    if (!ready_) return false;
    CollLock l;
    File f = LittleFS.open(COLL_TMP, "w");
    if (!f) return false;
    bool ok = write(f);
    f.close();
    if (!ok) {
        LittleFS.remove(COLL_TMP);
        return false;
    }
    if (exists(COLL_BAK)) LittleFS.remove(COLL_BAK);
    if (exists(COLL)) LittleFS.rename(COLL, COLL_BAK);
    LittleFS.rename(COLL_TMP, COLL);
    ++rev_;
    return true;
}

uint32_t coll_rev() { return rev_; }

}  // namespace storage
