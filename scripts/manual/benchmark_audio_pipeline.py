"""Opt-in, offline FFmpeg benchmark. Never imports the bot or its cache manager."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from music.loudness import (
    LOUDNORM_TARGET,
    measurement_command,
    normalization_command,
    opus_encode_options,
    parse_loudness_stats,
)


VARIANTS = ("default", "filter1", "filter2")
TIMEOUT = 180
_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"
_BENCH = re.compile(
    rf"bench:\s+utime=({_NUMBER})s\s+stime=({_NUMBER})s\s+rtime=({_NUMBER})s"
)


class BenchmarkError(RuntimeError):
    """Safe diagnostic message without raw subprocess output or private paths."""


@dataclass
class Result:
    wall: float
    stdout: str
    stderr: str
    user: float | None = None
    system: float | None = None
    real: float | None = None


def parse_benchmark(stderr):
    match = _BENCH.search(stderr)
    if not match:
        return None
    values = tuple(float(value) for value in match.groups())
    return values if all(math.isfinite(value) for value in values) else None


def run_command(command, *, timeout=TIMEOUT):
    environment = os.environ.copy()
    # FFREPORT can otherwise cause an unsolicited file write outside our temp dir.
    environment.pop("FFREPORT", None)
    environment["AV_LOG_FORCE_NOCOLOR"] = "1"
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=environment,
        )
    except subprocess.TimeoutExpired:
        raise BenchmarkError("Qua thoi gian cho FFmpeg/ffprobe; phep thu da dung.") from None
    except OSError:
        raise BenchmarkError("Khong khoi dong duoc FFmpeg/ffprobe. Kiem tra cai dat va PATH.") from None
    wall = time.perf_counter() - started
    if result.returncode:
        raise BenchmarkError(f"FFmpeg/ffprobe that bai (exit={result.returncode}); khong tinh la ket qua dat.")
    timing = parse_benchmark(result.stderr) or (None, None, None)
    return Result(wall, result.stdout, result.stderr, *timing)


def instrument(command, executable, variant="default"):
    if variant not in VARIANTS:
        raise ValueError("Unknown benchmark variant")
    flags = [] if variant == "default" else ["-filter_threads", variant[-1]]
    # Only logging/instrumentation and filter thread count differ from production.
    return [executable, "-hide_banner", "-nostdin", "-nostats", "-benchmark", *flags, *command[1:]]


def fixture_command(executable, destination, seconds):
    # Deterministic, different L/R signals with multiple frequencies and envelopes.
    # This is NOT a recording or a perceptual music-quality test.
    left = "0.08*(0.65+0.35*sin(2*PI*0.17*t))*(sin(2*PI*220*t)+0.35*sin(2*PI*880*t)+0.15*sin(2*PI*3520*t))"
    right = "0.08*(0.65+0.35*sin(2*PI*0.13*t))*(sin(2*PI*330*t)+0.35*sin(2*PI*1320*t)+0.15*sin(2*PI*5280*t))"
    return [
        executable, "-hide_banner", "-nostdin", "-nostats", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={left}|{right}:s=48000:d={seconds}",
        *opus_encode_options(), str(destination),
    ]


def decode_command(source):
    return ["ffmpeg", "-i", str(source), "-vn", "-f", "null", "-"]


def encode_control_command(source, destination):
    # Includes decoding the compressed source; not a pure encoder microbenchmark.
    return ["ffmpeg", "-y", "-i", str(source), "-vn", *opus_encode_options(), str(destination)]


def probe_output(executable, path, seconds):
    result = run_command([
        executable, "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,sample_rate,channels:format=duration",
        "-of", "json", str(path),
    ], timeout=30)
    try:
        info = json.loads(result.stdout)
        streams = info["streams"]
        duration = float(info["format"]["duration"])
        valid = (
            len(streams) == 1 and streams[0]["codec_type"] == "audio"
            and streams[0]["codec_name"] == "opus"
            and int(streams[0]["sample_rate"]) == 48000 and streams[0]["channels"] == 2
            and math.isfinite(duration) and abs(duration - seconds) <= 0.15
        )
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        valid = False
    if not valid:
        raise BenchmarkError("File thu khong dat: can Opus, stereo, 48 kHz va du thoi luong.")
    return duration


def normalization_mode(stderr):
    match = re.search(r"^\s*Normalization Type:\s*(Linear|Dynamic)\s*$", stderr, re.MULTILINE | re.IGNORECASE)
    if not match:
        raise BenchmarkError("FFmpeg khong bao che do linear/dynamic; khong the so sanh an toan.")
    return match.group(1).lower()


def validate_loudness(stats):
    if stats is None:
        raise BenchmarkError("Khong doc duoc so do am luong hop le.")
    target = {key: float(value) for key, value in (part.split("=") for part in LOUDNORM_TARGET.split(":"))}
    try:
        loudness, peak = float(stats["input_i"]), float(stats["input_tp"])
    except (ValueError, TypeError, KeyError, OverflowError):
        raise BenchmarkError("Khong doc duoc so do am luong hop le.") from None
    if not math.isfinite(loudness) or not math.isfinite(peak):
        raise BenchmarkError("So do am luong khong huu han; khong tinh la ket qua dat.")
    # Lossy Opus can slightly move decoded true peaks. No production limit changes.
    if abs(loudness - target["I"]) > 0.5 or peak > target["TP"] + 0.3:
        raise BenchmarkError(f"Dau ra chua dat nguong thu: I={loudness:.2f} LUFS, TP={peak:.2f} dBTP.")
    return loudness, peak


def variant_order(index):
    offset = index % len(VARIANTS)
    return VARIANTS[offset:] + VARIANTS[:offset]


def median_range(values):
    return f"{statistics.median(values):.3f} [{min(values):.3f}..{max(values):.3f}]"


def print_summary(records, quality, repeats):
    print("\n=== KET QUA: trung vi [min..max], don vi giay ===", flush=True)
    print("Buoc / cau hinh                   Wall                 CPU user  CPU sys   FF real")
    keys = [("decode", "default"), ("decode+encode", "default")]
    keys += [(stage, variant) for variant in VARIANTS for stage in ("measure", "normalize+encode")]
    for key in keys:
        samples = records[key]
        cpu = []
        for field in ("user", "system", "real"):
            values = [getattr(sample, field) for sample in samples]
            cpu.append(f"{statistics.median(values):8.3f}" if all(value is not None for value in values) else "     N/A")
        print(f"{key[0] + '/' + key[1]:32} {median_range([sample.wall for sample in samples]):22} {' '.join(cpu)}")
    print("\nTong hai buoc MEASURE + NORMALIZE/ENCODE (khong gom kiem tra dau ra):")
    totals = {}
    for variant in VARIANTS:
        totals[variant] = [a.wall + b.wall for a, b in zip(records[("measure", variant)], records[("normalize+encode", variant)])]
        values = quality[variant]
        modes = ",".join(sorted({row[0] for row in values}))
        print(f"{variant:8} {median_range(totals[variant])} | mode={modes} | I={min(row[1] for row in values):.2f}..{max(row[1] for row in values):.2f} LUFS | TP max={max(row[2] for row in values):.2f} dBTP | PASS")
    baseline = statistics.median(totals["default"])
    for variant in VARIANTS[1:]:
        change = 100 * (baseline - statistics.median(totals[variant])) / baseline if baseline else 0
        print(f"{variant}: nhanh hon default {change:+.1f}% tren MAU THU (so am = cham hon).")
    if repeats < 3:
        print("CHU Y: duoi 3 lan lap, chi nen xem la smoke test, chua ket luan hieu nang.")
    print("Wall gom khoi dong tien trinh; FF real/user/sys do FFmpeg bao, N/A neu khong co.")
    print("CPU user + sys co the lon hon wall khi dung nhieu luong; day khong phai tai CPU toan VPS.")
    print("Decode da nam trong cac buoc khac: KHONG cong tat ca cac dong. Decode+encode khong co loudnorm.")
    print("PASS chi kiem tra ky thuat mau gia, KHONG chung minh chat luong cam nhan tren moi bai nhac.")
    print("Khong tu dong chon cau hinh hay thay doi bot. Chenh lech nho/chong lap min-max can thu lai.")


def check_consistency(quality):
    rows = [row for samples in quality.values() for row in samples]
    if len({row[0] for row in rows}) != 1:
        raise BenchmarkError("Che do linear/dynamic khac nhau giua cac luot; khong so sanh nhu cung phep xu ly.")
    if max(row[1] for row in rows) - min(row[1] for row in rows) > 0.1 or max(row[2] for row in rows) - min(row[2] for row in rows) > 0.2:
        raise BenchmarkError("So do dau ra khac nhau dang ke giua cac luot; can kiem tra truoc khi toi uu.")


def benchmark(executable, probe, seconds, repeats):
    records = {(stage, variant): [] for variant in VARIANTS for stage in ("measure", "normalize+encode")}
    records.update({("decode", "default"): [], ("decode+encode", "default"): []})
    quality = {variant: [] for variant in VARIANTS}
    with tempfile.TemporaryDirectory(prefix="peto-audio-bench-") as directory:
        source = Path(directory) / "synthetic-source.opus"
        output = Path(directory) / "trial.opus"
        control = Path(directory) / "control.opus"
        print("Tao mau stereo Opus co dinh (khong tinh vao thoi gian)...", flush=True)
        run_command(fixture_command(executable, source, seconds))
        probe_output(probe, source, seconds)

        def pipeline(variant):
            measured = run_command(instrument(measurement_command(str(source)), executable, variant))
            stats = parse_loudness_stats(measured.stderr)
            if stats is None:
                raise BenchmarkError("Do am luong that bai; khong am tham chuyen sang single-pass.")
            encoded = run_command(instrument(normalization_command(str(source), str(output), stats), executable, variant))
            mode = normalization_mode(encoded.stderr)
            # Independent verification always uses default; never included in timing.
            probe_output(probe, output, seconds)
            checked = run_command(instrument(measurement_command(str(output)), executable))
            loudness, peak = validate_loudness(parse_loudness_stats(checked.stderr))
            return measured, encoded, (mode, loudness, peak)

        print("Lam nong 1 luot default, kem kiem tra dau ra (khong tinh)...", flush=True)
        pipeline("default")
        for index in range(repeats):
            print(f"\nLan {index + 1}/{repeats}: decode va decode+encode khong loudnorm...", flush=True)
            records[("decode", "default")].append(run_command(instrument(decode_command(source), executable)))
            records[("decode+encode", "default")].append(run_command(instrument(encode_control_command(source, control), executable)))
            probe_output(probe, control, seconds)
            for variant in variant_order(index):
                print(f"  {variant}: do, ma hoa, roi kiem tra dau ra...", flush=True)
                measured, encoded, checked = pipeline(variant)
                records[("measure", variant)].append(measured)
                records[("normalize+encode", variant)].append(encoded)
                quality[variant].append(checked)
                print(f"  OK {variant}: do={measured.wall:.3f}s, ma hoa={encoded.wall:.3f}s, mode={checked[0]}", flush=True)
        check_consistency(quality)
        print_summary(records, quality, repeats)
    print("\nDa don file thu. Khong doc .env, khong dung mang, khong sua cache nhac/config cua Peto.")


def bounded_int(minimum, maximum):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Can so nguyen.") from None
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"Can nam trong {minimum}..{maximum}.")
        return number
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Do hieu nang FFmpeg offline; khong thay doi cau hinh Peto.")
    parser.add_argument("--seconds", type=bounded_int(10, 300), default=120, help="Do dai mau gia (mac dinh 120 giay)")
    parser.add_argument("--repeats", type=bounded_int(1, 5), default=3, help="So lan lap (mac dinh 3)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Executable FFmpeg, mac dinh tu PATH")
    parser.add_argument("--ffprobe", default="ffprobe", help="Executable ffprobe, mac dinh tu PATH")
    args = parser.parse_args(argv)
    executable, probe = shutil.which(args.ffmpeg), shutil.which(args.ffprobe)
    if not executable or not probe:
        print("LOI: can ffmpeg va ffprobe trong PATH (hoac truyen duong dan qua --ffmpeg/--ffprobe).")
        return 1
    try:
        for label, path in (("ffmpeg", executable), ("ffprobe", probe)):
            version = run_command([path, "-version"], timeout=15).stdout
            match = re.search(r"version ([\w.+-]+)", version)
            print(f"{label}: {match.group(1) if match else 'unknown'}")
        print(f"CPU logic: {os.cpu_count()}; mau={args.seconds}s; lap={args.repeats}; target={LOUDNORM_TARGET}; Opus=160k/FEC10")
        print("Chi thay -filter_threads default/1/2. Default la tu dong, KHONG phai 1 luong.")
        print("Nen chay luc bot ranh: phep thu van dung CPU, co the mat vai phut. Ctrl+C de dung.", flush=True)
        benchmark(executable, probe, args.seconds, args.repeats)
    except BenchmarkError as error:
        print(f"LOI: {error}")
        return 1
    except KeyboardInterrupt:
        print("\nDa huy phep thu; file tam duoc don, cau hinh bot khong doi.")
        return 130
    except OSError:
        print("LOI: khong thao tac duoc thu muc/file tam; kiem tra dung luong va quyen truy cap.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
