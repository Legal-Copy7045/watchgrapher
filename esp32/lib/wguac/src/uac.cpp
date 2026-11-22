#include "uac.h"

#include <algorithm>

namespace uac {

namespace {
constexpr uint8_t DT_INTERFACE = 0x04, DT_ENDPOINT = 0x05;
constexpr uint8_t CS_INTERFACE = 0x24, CS_ENDPOINT = 0x25;
constexpr uint8_t CLASS_AUDIO = 0x01, SUB_CONTROL = 0x01, SUB_STREAMING = 0x02;
constexpr uint8_t AS_GENERAL = 0x01, FORMAT_TYPE = 0x02;
constexpr uint8_t AC_FEATURE_UNIT = 0x06, AC2_CLOCK_SOURCE = 0x0A;
constexpr uint8_t PROTOCOL_UAC2 = 0x20;

inline uint16_t le16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }
inline uint32_t le24(const uint8_t* p) { return (uint32_t)(p[0] | (p[1] << 8) | (p[2] << 16)); }
inline uint32_t le32(const uint8_t* p) { return le24(p) | ((uint32_t)p[3] << 24); }
}  // namespace

bool parse_config(const uint8_t* d, size_t len, Device& out) {
    out = Device();
    // Where we are: the current interface and whether it is audio control or
    // streaming, and the alt-setting being built.
    int cur_class = -1, cur_sub = -1;
    AltFormat cur;
    bool in_stream_alt = false, have_fmt = false, pcm = true;
    uint8_t uac2_channels = 0;

    auto flush = [&]() {
        if (in_stream_alt && have_fmt && pcm && cur.ep && cur.mps && cur.channels &&
            cur.subframe >= 2 && cur.subframe <= 4)
            out.alts.push_back(cur);
        in_stream_alt = false;
        have_fmt = false;
        pcm = true;
        uac2_channels = 0;
    };

    for (size_t i = 0; i + 2 <= len;) {
        uint8_t bl = d[i], bt = d[i + 1];
        if (bl < 2 || i + bl > len) break;   // malformed: stop rather than read past the end
        const uint8_t* p = d + i;

        if (bt == DT_INTERFACE && bl >= 9) {
            flush();
            cur_class = p[5];
            cur_sub = p[6];
            if (cur_class == CLASS_AUDIO && cur_sub == SUB_CONTROL && !out.has_ac) {
                out.has_ac = true;
                out.ac_iface = p[2];
            }
            if (cur_class == CLASS_AUDIO && cur_sub == SUB_STREAMING) {
                if (p[7] == PROTOCOL_UAC2) out.uac2 = true;
                cur = AltFormat();
                cur.iface = p[2];
                cur.alt = p[3];
                in_stream_alt = p[3] != 0 && p[4] > 0;   // alt 0 is the zero-bandwidth setting
            }
        } else if (bt == CS_INTERFACE && bl >= 3 && cur_class == CLASS_AUDIO) {
            uint8_t sub = p[2];
            if (cur_sub == SUB_CONTROL) {
                if (sub == AC2_CLOCK_SOURCE && bl >= 4 && !out.clock_id) out.clock_id = p[3];
                if (sub == AC_FEATURE_UNIT && bl >= 4 && !out.feature_unit) out.feature_unit = p[3];
            } else if (cur_sub == SUB_STREAMING && in_stream_alt) {
                if (sub == AS_GENERAL) {
                    if (out.uac2 && bl >= 11) {
                        // bFormatType(5) bmFormats(6..9) bNrChannels(10); PCM is bit 0.
                        pcm = (le32(p + 6) & 1u) != 0;
                        uac2_channels = p[10];
                    } else if (!out.uac2 && bl >= 7) {
                        pcm = le16(p + 5) == 0x0001;   // wFormatTag PCM
                    }
                } else if (sub == FORMAT_TYPE && bl >= 4 && p[3] == 1 /* Type I */) {
                    if (out.uac2 && bl >= 6) {
                        cur.subframe = p[4];
                        cur.bits = p[5];
                        cur.channels = uac2_channels;
                        have_fmt = true;
                    } else if (!out.uac2 && bl >= 8) {
                        cur.channels = p[4];
                        cur.subframe = p[5];
                        cur.bits = p[6];
                        uint8_t n = p[7];
                        if (n == 0 && bl >= 14) {
                            cur.continuous = true;
                            cur.rate_lo = le24(p + 8);
                            cur.rate_hi = le24(p + 11);
                        } else {
                            for (uint8_t k = 0; k < n && 8 + 3 * k + 3 <= bl; ++k)
                                cur.rates.push_back(le24(p + 8 + 3 * k));
                        }
                        have_fmt = true;
                    }
                }
            }
        } else if (bt == DT_ENDPOINT && bl >= 7 && in_stream_alt) {
            uint8_t addr = p[2], attr = p[3];
            if ((attr & 0x03) == 0x01 && (addr & 0x80) && !cur.ep) {   // isochronous IN
                cur.ep = addr;
                cur.mps = le16(p + 4) & 0x7FF;
                cur.interval = p[6] ? p[6] : 1;
            }
        } else if (bt == CS_ENDPOINT && bl >= 4 && in_stream_alt && !out.uac2) {
            if (p[2] == 0x01) cur.freq_control = (p[3] & 0x01) != 0;   // EP_GENERAL
        }
        i += bl;
    }
    flush();
    return !out.alts.empty();
}

bool parse_rate_ranges(const uint8_t* data, size_t len, std::vector<Span>& out) {
    out.clear();
    if (len < 2) return false;
    uint16_t n = le16(data);
    for (uint16_t k = 0; k < n && 2 + 12 * (size_t)k + 12 <= len; ++k) {
        const uint8_t* p = data + 2 + 12 * k;
        uint32_t lo = le32(p), hi = le32(p + 4);
        if (lo && hi >= lo) out.push_back({lo, hi});
    }
    return !out.empty();
}

bool supports(const AltFormat& a, uint32_t rate) {
    if (a.continuous) return rate >= a.rate_lo && rate <= a.rate_hi;
    if (a.rates.empty()) return false;
    return std::find(a.rates.begin(), a.rates.end(), rate) != a.rates.end();
}

bool supports(const std::vector<Span>& spans, uint32_t rate) {
    for (const Span& s : spans)
        if (rate >= s.lo && rate <= s.hi) return true;
    return false;
}

bool choose(const Device& d, uint32_t preferred, uint32_t min_rate,
            const std::vector<Span>& uac2_spans, Choice& out) {
    // Candidate rates, best first: the analysis rate, then the standard
    // rates below it, then whatever else the device lists below it.
    std::vector<uint32_t> want = {preferred};
    for (uint32_t r : {48000u, 44100u, 32000u})
        if (r < preferred) want.push_back(r);

    int best = -1;
    uint32_t best_rate = 0;
    int best_score = -1;
    for (size_t i = 0; i < d.alts.size(); ++i) {
        const AltFormat& a = d.alts[i];
        if (a.channels < 1 || a.channels > 8) continue;
        // Must fit in one packet per interval at full speed.
        uint32_t rate = 0;
        std::vector<uint32_t> cands = want;
        if (!d.uac2)
            for (uint32_t r : a.rates)
                if (r < preferred && r >= min_rate) cands.push_back(r);
        for (uint32_t r : cands) {
            bool ok;
            if (d.uac2) ok = uac2_spans.empty() ? (r == preferred || r == 48000 || r == 44100)
                                                : supports(uac2_spans, r);
            else ok = supports(a, r) || (a.rates.empty() && !a.continuous && r == preferred);
            if (ok && r >= min_rate && r <= preferred) { rate = r; break; }
        }
        if (!rate) continue;
        uint32_t need = (rate + 999) / 1000 * a.channels * a.subframe;
        if (need > a.mps) continue;
        // Prefer the analysis rate, then mono (less to move), then more bits.
        int score = (rate == preferred ? 1000 : (int)(rate / 100)) * 100 +
                    (a.channels == 1 ? 50 : 0) + a.bits;
        if (score > best_score) { best_score = score; best = (int)i; best_rate = rate; }
    }
    if (best < 0) return false;
    out.alt_index = (size_t)best;
    out.rate = best_rate;
    return true;
}

size_t to_float(const uint8_t* p, size_t nbytes, uint8_t channels, uint8_t subframe,
                float* out, size_t max_out) {
    if (!channels || !subframe) return 0;
    const size_t frame = (size_t)channels * subframe;
    size_t n = std::min(nbytes / frame, max_out);
    for (size_t i = 0; i < n; ++i, p += frame) {
        int32_t v;
        switch (subframe) {
            case 2: v = (int32_t)(int16_t)le16(p) * 65536; break;
            case 3: v = (int32_t)(le24(p) << 8); break;
            default: v = (int32_t)le32(p); break;
        }
        out[i] = (float)v * (1.0f / 2147483648.0f);
    }
    return n;
}

}  // namespace uac
