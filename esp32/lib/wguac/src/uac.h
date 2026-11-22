// USB Audio Class (UAC1 / UAC2) helpers for a USB microphone: descriptor
// parsing, choosing a stream format, and converting packets to float.
//
// Portable -- no ESP-IDF -- so the parsing is unit-tested on the host
// (esp32/host/uac_test.cpp). The USB transfers live in src/usb_audio.cpp.
#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace uac {

// One candidate streaming format: an alternate setting of an AudioStreaming
// interface with an isochronous IN endpoint carrying Type I PCM.
struct AltFormat {
    uint8_t iface = 0;          // AudioStreaming interface number
    uint8_t alt = 0;            // its alternate setting
    uint8_t ep = 0;             // isochronous IN endpoint address
    uint16_t mps = 0;           // endpoint max packet size (bytes)
    uint8_t interval = 1;       // bInterval
    uint8_t channels = 0;
    uint8_t subframe = 0;       // bytes per sample per channel (2, 3 or 4)
    uint8_t bits = 0;           // bit resolution
    bool freq_control = false;  // UAC1: endpoint accepts SET_CUR sampling frequency
    // UAC1 sample rates: discrete list, or a continuous [lo, hi] range.
    std::vector<uint32_t> rates;
    bool continuous = false;
    uint32_t rate_lo = 0, rate_hi = 0;
};

struct Device {
    bool uac2 = false;          // from the AudioStreaming interface protocol
    uint8_t ac_iface = 0;       // AudioControl interface number
    bool has_ac = false;
    uint8_t clock_id = 0;       // UAC2 clock source feeding the stream (0 = none found)
    uint8_t feature_unit = 0;   // first feature unit (for unmuting), 0 = none
    std::vector<AltFormat> alts;
};

// Parse a full configuration descriptor (as returned by GET_DESCRIPTOR).
// Returns false if it has no usable audio input stream.
bool parse_config(const uint8_t* desc, size_t len, Device& out);

// Parse a UAC2 RANGE reply for the sampling-frequency control
// (wNumSubRanges, then {min, max, res} as 32-bit LE triples) into a list of
// supported [lo, hi] spans.
struct Span { uint32_t lo, hi; };
bool parse_rate_ranges(const uint8_t* data, size_t len, std::vector<Span>& out);

bool supports(const AltFormat& a, uint32_t rate);
bool supports(const std::vector<Span>& spans, uint32_t rate);

// Pick the format to stream and the rate to run it at. `preferred` is the
// device's analysis rate; a lower rate is acceptable down to `min_rate`,
// never a higher one (the analysis buffers were sized for `preferred`).
// For UAC2, `uac2_spans` lists the clock's rates (empty = unknown: assume
// `preferred`, then 48000/44100). Returns false if nothing fits.
struct Choice { size_t alt_index; uint32_t rate; };
bool choose(const Device& d, uint32_t preferred, uint32_t min_rate,
            const std::vector<Span>& uac2_spans, Choice& out);

// Convert `nbytes` of interleaved PCM to mono float (+/-1), taking the first
// channel. Returns samples written.
size_t to_float(const uint8_t* data, size_t nbytes, uint8_t channels, uint8_t subframe,
                float* out, size_t max_out);

}  // namespace uac
