// The LittleFS store (collection, power-reserve logs), mounted in the
// background: the first start after an install formats it, which erases the
// whole partition and takes minutes -- Wi-Fi and the app must not wait for it.
#pragma once

namespace storage {
// Start mounting; `after` runs (on the mount task) once it is usable.
void begin(void (*after)());
bool ready();
}  // namespace storage
