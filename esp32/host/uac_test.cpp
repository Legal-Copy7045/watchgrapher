// Host tests for the USB Audio Class descriptor parser (lib/wguac).
#include <cmath>
#include <cstdio>
#include <vector>

#include "uac.h"

static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); ++fails; } } while (0)

using B = std::vector<uint8_t>;
static void add(B& b, std::initializer_list<int> v) { for (int x : v) b.push_back((uint8_t)x); }
static void r24(B& b, uint32_t r) { add(b, {int(r & 255), int((r >> 8) & 255), int((r >> 16) & 255)}); }

// A typical UAC1 USB microphone: 16-bit mono at 48 / 44.1 kHz, plus a 24-bit
// stereo alternate setting.
static B uac1_mic() {
    B b;
    add(b, {9, 2, 0, 0, 2, 1, 0, 0x80, 50});                 // config (total length patched below)
    add(b, {9, 4, 0, 0, 0, 1, 1, 0, 0});                     // AC interface 0
    add(b, {9, 0x24, 1, 0, 1, 30, 0, 1, 1});                 // AC header
    add(b, {12, 0x24, 2, 1, 1, 2, 0, 1, 0, 0, 0, 0});        // input terminal (mic)
    add(b, {9, 0x24, 6, 2, 1, 1, 3, 0, 0});                  // feature unit id 2
    add(b, {9, 0x24, 3, 3, 1, 1, 0, 2, 0});                  // output terminal
    add(b, {9, 4, 1, 0, 0, 1, 2, 0, 0});                     // AS interface 1 alt 0 (no EP)
    add(b, {9, 4, 1, 1, 1, 1, 2, 0, 0});                     // alt 1
    add(b, {7, 0x24, 1, 3, 1, 1, 0});                        // AS_GENERAL, PCM
    add(b, {14, 0x24, 2, 1, 1, 2, 16, 2}); r24(b, 48000); r24(b, 44100);
    add(b, {9, 5, 0x82, 0x05, 100, 0, 1, 0, 0});             // iso IN, mps 100
    add(b, {7, 0x25, 1, 1, 0, 0, 0});                        // EP_GENERAL: freq control
    add(b, {9, 4, 1, 2, 1, 1, 2, 0, 0});                     // alt 2: 24-bit stereo
    add(b, {7, 0x24, 1, 3, 1, 1, 0});
    add(b, {11, 0x24, 2, 1, 2, 3, 24, 1}); r24(b, 48000);
    add(b, {9, 5, 0x82, 0x05, 0x20, 1, 1, 0, 0});            // mps 288
    add(b, {7, 0x25, 1, 1, 0, 0, 0});
    b[2] = b.size() & 255; b[3] = b.size() >> 8;
    return b;
}

// A UAC2 microphone: 24-bit stereo, clock source id 4.
static B uac2_mic() {
    B b;
    add(b, {9, 2, 0, 0, 2, 1, 0, 0x80, 50});
    add(b, {8, 0x0B, 0, 2, 1, 0, 0x20, 0});                  // interface association
    add(b, {9, 4, 0, 0, 0, 1, 1, 0x20, 0});                  // AC, protocol UAC2
    add(b, {9, 0x24, 1, 0, 2, 3, 60, 0, 0});                 // AC header
    add(b, {8, 0x24, 0x0A, 4, 1, 7, 0, 0});                  // clock source id 4
    add(b, {17, 0x24, 2, 1, 1, 2, 0, 4, 2, 0, 0, 0, 0, 0, 0, 0, 0});   // input terminal
    add(b, {18, 0x24, 6, 5, 1, 0x0F, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0});  // feature unit 5
    add(b, {9, 4, 1, 0, 0, 1, 2, 0x20, 0});                  // AS alt 0
    add(b, {9, 4, 1, 1, 1, 1, 2, 0x20, 0});                  // AS alt 1
    add(b, {16, 0x24, 1, 3, 0, 1, 1, 0, 0, 0, 2, 3, 0, 0, 0, 0});   // AS_GENERAL: PCM, 2 ch
    add(b, {6, 0x24, 2, 1, 3, 24});                          // Type I, 3-byte subslot
    add(b, {7, 5, 0x81, 0x05, 0x20, 1, 1});                  // iso IN, mps 288
    add(b, {8, 0x25, 1, 0, 0, 0, 0, 0});
    b[2] = b.size() & 255; b[3] = b.size() >> 8;
    return b;
}

int main() {
    {
        B d = uac1_mic();
        uac::Device dev;
        CHECK(uac::parse_config(d.data(), d.size(), dev));
        CHECK(!dev.uac2);
        CHECK(dev.has_ac && dev.ac_iface == 0);
        CHECK(dev.feature_unit == 2);
        CHECK(dev.alts.size() == 2);
        CHECK(dev.alts[0].iface == 1 && dev.alts[0].alt == 1 && dev.alts[0].ep == 0x82);
        CHECK(dev.alts[0].channels == 1 && dev.alts[0].subframe == 2 && dev.alts[0].bits == 16);
        CHECK(dev.alts[0].rates.size() == 2 && dev.alts[0].freq_control);
        CHECK(dev.alts[1].mps == 288 && dev.alts[1].channels == 2 && dev.alts[1].subframe == 3);
        uac::Choice c;
        CHECK(uac::choose(dev, 48000, 32000, {}, c));
        CHECK(c.rate == 48000 && c.alt_index == 0);      // mono preferred
        CHECK(uac::choose(dev, 44100, 32000, {}, c));
        CHECK(c.rate == 44100 && c.alt_index == 0);      // alt 2 has no 44.1k
        CHECK(!uac::choose(dev, 22050, 16000, {}, c));   // nothing at or below 22.05k
        // Truncated descriptor: must not crash, and must not invent a stream.
        uac::Device t;
        uac::parse_config(d.data(), 60, t);
        CHECK(t.alts.empty());
    }
    {
        B d = uac2_mic();
        uac::Device dev;
        CHECK(uac::parse_config(d.data(), d.size(), dev));
        CHECK(dev.uac2 && dev.clock_id == 4 && dev.feature_unit == 5);
        CHECK(dev.alts.size() == 1);
        CHECK(dev.alts[0].channels == 2 && dev.alts[0].subframe == 3 && dev.alts[0].bits == 24);
        // RANGE reply: 44.1k and 48k discrete
        B r = {2, 0};
        auto r32 = [&](uint32_t v) { for (int k = 0; k < 4; ++k) r.push_back((v >> (8 * k)) & 255); };
        r32(44100); r32(44100); r32(0); r32(48000); r32(48000); r32(0);
        std::vector<uac::Span> spans;
        CHECK(uac::parse_rate_ranges(r.data(), r.size(), spans) && spans.size() == 2);
        uac::Choice c;
        CHECK(uac::choose(dev, 48000, 32000, spans, c) && c.rate == 48000);
        std::vector<uac::Span> only16 = {{16000, 16000}};
        CHECK(!uac::choose(dev, 48000, 32000, only16, c));
    }
    {
        // Conversion: 16-bit stereo, first channel taken.
        uint8_t p[8] = {0x00, 0x40, 0xFF, 0x7F, 0x00, 0xC0, 0x01, 0x00};
        float f[4];
        size_t n = uac::to_float(p, 8, 2, 2, f, 4);
        CHECK(n == 2 && std::fabs(f[0] - 0.5f) < 1e-6 && std::fabs(f[1] + 0.5f) < 1e-6);
        uint8_t q[6] = {0x00, 0x00, 0x80, 0xFF, 0xFF, 0x7F};   // 24-bit mono: -1, ~+1
        n = uac::to_float(q, 6, 1, 3, f, 4);
        CHECK(n == 2 && std::fabs(f[0] + 1.0f) < 1e-6 && f[1] > 0.9999f);
    }
    {
        // Not an audio device at all.
        B b;
        add(b, {9, 2, 18, 0, 1, 1, 0, 0x80, 50});
        add(b, {9, 4, 0, 0, 1, 3, 1, 1, 0});                  // HID
        uac::Device dev;
        CHECK(!uac::parse_config(b.data(), b.size(), dev));
    }
    printf(fails ? "%d failure(s)\n" : "uac tests passed\n", fails);
    return fails ? 1 : 0;
}
