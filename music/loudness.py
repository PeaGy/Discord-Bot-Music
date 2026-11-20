"""Pure loudness parsing and FFmpeg command construction shared by diagnostics."""

import json
import math
import re


LOUDNORM_TARGET = "I=-16:TP=-1.5:LRA=11"
OPUS_EXPECTED_PACKET_LOSS = 10

_LOUDNESS_FIELDS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
_LOUDNESS_FIELD_MARKER = re.compile(r'"(?:' + "|".join(_LOUDNESS_FIELDS) + r')"\s*:')


def parse_loudness_stats(stderr):
    """Read one complete loudnorm report, independent of surrounding FFmpeg logs.

    raw_decode stops at the closing JSON brace; json.loads on the whole stderr
    tail rejects valid reports followed by muxer/progress messages. Unrelated
    JSON is ignored, but ambiguous or broken loudnorm reports aren't reused.
    """
    if not isinstance(stderr, str):
        return None
    decoder = json.JSONDecoder()
    stats = None
    position = 0
    while True:
        start = stderr.find("{", position)
        if start < 0:
            return stats
        try:
            value, end = decoder.raw_decode(stderr, start)
        except (json.JSONDecodeError, RecursionError):
            # Limit the lookahead to this apparent object. A stray brace in an
            # earlier log line must not swallow the genuine report after it.
            next_start = stderr.find("{", start + 1)
            fragment = stderr[start:next_start if next_start >= 0 else len(stderr)]
            if _LOUDNESS_FIELD_MARKER.search(fragment):
                return None
            position = start + 1
            continue
        position = end
        if not isinstance(value, dict) or not any(key in value for key in _LOUDNESS_FIELDS):
            continue
        if stats is not None or not all(key in value for key in _LOUDNESS_FIELDS):
            return None
        parsed = {}
        for key in _LOUDNESS_FIELDS:
            number = value[key]
            if isinstance(number, bool) or not isinstance(number, (str, int, float)):
                return None
            try:
                number = float(number)
            except (ValueError, TypeError, OverflowError):
                return None
            if not math.isfinite(number):
                # Silence can produce -inf. It has no usable two-pass gain;
                # keep the existing single-pass fallback instead of failing.
                return None
            parsed[key] = format(number, ".12g")
        stats = parsed


def opus_encode_options():
    """Return a fresh list preserving the cache's existing Opus quality options."""
    return [
        "-c:a", "libopus", "-b:a", "160k",
        "-application", "audio",
        "-frame_duration", "20",
        "-packet_loss", str(OPUS_EXPECTED_PACKET_LOSS),
        "-fec", "1",
    ]


def measurement_command(filepath):
    """Build the existing pass-one measurement command without running it."""
    return [
        "ffmpeg", "-i", filepath,
        "-af", f"loudnorm={LOUDNORM_TARGET}:print_format=json",
        "-f", "null", "-",
    ]


def fast_measurement_command(filepath):
    return ["ffmpeg", "-nostats", "-i", filepath, "-vn", "-af",
            "ebur128=peak=true:framelog=verbose", "-f", "null", "-"]


def parse_linear_measurement(stderr):
    """Return a gain only for measurements safely inside the linear limits."""
    summaries = re.split(r"\bSummary:\s*\n", stderr)
    if len(summaries) != 2:
        return None
    stats = {}
    for name, label, unit in (("input_i", "I", "LUFS"), ("input_tp", "Peak", "dBFS"),
                              ("input_lra", "LRA", "LU")):
        values = re.findall(rf"^\s*{label}:\s*(-?\d+(?:\.\d+)?)\s+{unit}\s*$",
                            summaries[1], re.MULTILINE)
        if len(values) != 1:
            return None
        stats[name] = float(values[0])
    targets = dict(part.split("=") for part in LOUDNORM_TARGET.split(":"))
    gain = float(targets["I"]) - stats["input_i"]
    # The summary rounds to tenths. Leave headroom at both decision boundaries.
    if not (-70 < stats["input_i"] < 0 and 0 < stats["input_lra"] < float(targets["LRA"])
            and stats["input_tp"] + gain <= float(targets["TP"]) - 0.3):
        return None
    return {**stats, "gain_db": gain}


def normalization_command(raw_path, final_path, stats):
    """Build the existing measured or fallback normalize-and-encode command."""
    if stats and "gain_db" in stats:
        loudnorm_filter = f"volume={stats['gain_db']:.2f}dB"
    elif stats:
        loudnorm_filter = (
            f"loudnorm={LOUDNORM_TARGET}:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true:print_format=summary"
        )
    else:
        # Không đo được (file lỗi / ffmpeg cũ) -> fallback single-pass, vẫn còn
        # hơn là không normalize gì cả
        loudnorm_filter = f"loudnorm={LOUDNORM_TARGET}:print_format=summary"

    return [
        "ffmpeg", "-y", "-i", raw_path,
        "-vn", "-af", loudnorm_filter,
        *opus_encode_options(),
        final_path,
    ]
