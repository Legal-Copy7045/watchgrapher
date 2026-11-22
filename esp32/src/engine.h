// The measurement engine: runs the DSP on the newest audio in a loop, and
// owns the run state -- live listening, timed runs, and power-reserve logs.
#pragma once

#include <Arduino.h>

#include <functional>
#include <string>

namespace engine {

// `window_cap_s` is the longest analysis window PSRAM allows.
bool begin(float window_cap_s);
float window_cap_s();

// Wall clock. The device has no RTC: the browser sends its time with every
// poll, and NTP sets it when the device is on a network with internet.
void set_clock_ms(int64_t epoch_ms);
// Browser's Date.getTimezoneOffset(), so saved times read as local time the
// way the desktop app writes them.
void set_tz_offset_min(int minutes_west);
bool clock_valid();
String iso_now();   // "" if the clock is not set

using ArgFn = std::function<String(const char*)>;
// Run a command from POST /api/cmd. Returns a JSON reply.
std::string command(const String& action, const ArgFn& arg);

// Live state for GET /api/state. The (large) measurement block is included
// only when it changed since `since`.
void state_json(uint32_t since, std::string& out);
// Recent measurements, one per analysis, for the charts.
void history_json(std::string& out);
// Power-reserve runs stored on the device.
void pr_list_json(std::string& out);

// Called at boot: a power-reserve log that was running when the device lost
// power is closed off and marked as interrupted.
void recover_interrupted_pr();

}  // namespace engine
