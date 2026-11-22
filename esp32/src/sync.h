// Sync with the WatchGrapher desktop app on the same network.
//
// The desktop broadcasts a small UDP beacon (watchgrapher/devicesync.py); the
// device lists the computers it hears. Pairing sends the 6-digit code the
// desktop shows and keeps the token it returns. A sync sends the device's
// collection; the desktop merges it into its own (add-only) and replies with
// the merged collection, which replaces the device's. Both then hold
// everything. With auto-sync on, the device syncs when the paired computer
// appears, every 15 minutes, and soon after the collection changes.
#pragma once

#include <Arduino.h>

#include <string>

namespace dsync {

void begin();
void loop();   // call from the Arduino loop: auto-sync

// For GET /api/sync.
void status_json(std::string& out);

// Pair with a computer that has been seen, using the code it shows.
// Blocks for the HTTP round trip (a few seconds at most).
bool pair(const String& desktop_id, const String& code, String& msg);
// Start a sync in the background (no-op if one is running).
bool sync_now(String& msg);
void forget();
void set_auto(bool on);

}  // namespace dsync
