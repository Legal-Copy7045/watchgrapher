// The LittleFS store (collection, power-reserve logs), mounted in the
// background: the first start after an install formats it, which erases the
// whole partition and takes minutes -- Wi-Fi and the app must not wait for it.
#pragma once

#include <FS.h>

#include <functional>

namespace storage {
// Start mounting; `after` runs (on the mount task) once it is usable.
void begin(void (*after)());
bool ready();

// ---- the collection (the desktop's collection.json schema) ----
// Shared by the web app and desktop sync, so every access goes through here.
// Hold a CollLock while reading it out, so a replace can't swap it mid-read.
struct CollLock {
    CollLock();
    ~CollLock();
};
// Open the current collection (or its backup) for reading; false if none yet.
bool coll_open(File& f);
// Replace it: `write` fills a temp file; only if it succeeds does that become
// the collection (the old one is kept as a backup). Bumps the revision.
bool coll_replace(const std::function<bool(File&)>& write);
// Changes whenever the collection is replaced; the app reloads when it moves.
uint32_t coll_rev();
}  // namespace storage
