// Wi-Fi: the device's own hotspot and, optionally, your home network.
//
// With a home network set, the hotspot by default turns itself off once the
// device has joined it (so it is simply a device on your network), and comes
// back on whenever that network can't be reached -- so it still works away
// from home, on the bench or at a show.
#pragma once

#include <Arduino.h>

namespace net {

void begin();
void loop();                 // call often; manages the hotspot fallback
bool ready();                // begin() has finished

// Join a network now (Improv): saves it to settings on success. Blocks up to
// `timeout_ms`. Returns true and fills `url` when connected.
bool join(const String& ssid, const String& pass, uint32_t timeout_ms, String& url);

bool sta_connected();
String sta_ip();             // "" when not connected
String ap_ssid();
bool ap_on();
String status();             // one line for the UI

}  // namespace net
