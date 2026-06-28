from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

CHANNELS = (
    "Scintillator 1",
    "Scintillator 2",
    "Scintillator 3",
    "Scintillator 4",
)


def parse_detector_file(
    source: Path,
    *,
    bin_minutes: int = 10,
    clock_tick_us: float = 0.024,
    min_decay_us: float = 0.2,
    max_decay_us: float = 10.0,
) -> dict[str, int | float | str]:
    """Convert a QuarkNet-style detector text file into dashboard CSV files.

    The eight hex fields after the event clock are treated as four channel
    pairs. A channel is counted when either field in its pair is nonzero.
    The muon-lifetime plot is a candidate timing histogram made from
    consecutive event-cluster clock separations.
    """
    DATA_DIR.mkdir(exist_ok=True)
    bin_seconds = bin_minutes * 60

    bin_counts: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    cumulative = [0, 0, 0, 0]
    decay_times_us: list[float] = []
    malformed = 0
    exact_duplicates = 0
    parsed_events = 0
    channel_rows = 0
    first_epoch: int | None = None
    last_epoch: int | None = None
    previous_line: str | None = None
    previous_cluster_clock: int | None = None
    previous_raw_clock: int | None = None
    gps_second_cache: dict[tuple[str, str], int] = {}

    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line == previous_line:
                exact_duplicates += 1
                continue
            previous_line = line

            parts = line.split()
            if len(parts) < 16 or len(parts[0]) != 8:
                malformed += 1
                continue

            try:
                clock = int(parts[0], 16)
                values = [int(value, 16) for value in parts[1:9]]
                gps_key = (parts[10][0:6], parts[11])
                epoch_seconds = gps_second_cache.get(gps_key)
                if epoch_seconds is None:
                    epoch_seconds = _parse_gps_epoch_seconds(parts[10], parts[11])
                    gps_second_cache[gps_key] = epoch_seconds
            except ValueError:
                malformed += 1
                continue

            parsed_events += 1
            first_epoch = epoch_seconds if first_epoch is None else min(first_epoch, epoch_seconds)
            last_epoch = epoch_seconds if last_epoch is None else max(last_epoch, epoch_seconds)

            channel_hits = [
                int(bool(values[0] or values[1])),
                int(bool(values[2] or values[3])),
                int(bool(values[4] or values[5])),
                int(bool(values[6] or values[7])),
            ]

            if any(channel_hits):
                channel_rows += 1
                bin_start = epoch_seconds - (epoch_seconds % bin_seconds)
                for index, hit in enumerate(channel_hits):
                    bin_counts[bin_start][index] += hit

            cluster_clock = _cluster_clock(clock, previous_raw_clock, previous_cluster_clock)
            if previous_cluster_clock is not None and cluster_clock != previous_cluster_clock:
                delta_ticks = _clock_delta(cluster_clock, previous_cluster_clock)
                delta_us = delta_ticks * clock_tick_us
                if min_decay_us <= delta_us <= max_decay_us:
                    decay_times_us.append(delta_us)

            previous_raw_clock = clock
            previous_cluster_clock = cluster_clock

    _write_live_rates(bin_counts, cumulative, bin_minutes, first_epoch, last_epoch)
    _write_muon_lifetime(decay_times_us, min_decay_us, max_decay_us)
    _write_absorption(source, bin_counts, bin_minutes, first_epoch, last_epoch)

    status = {
        "current_log_file": str(source),
        "malformed_records": malformed,
        "exact_duplicate_records": exact_duplicates,
        "parsed_events": parsed_events,
        "channel_rows": channel_rows,
        "last_upload": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "first_data_timestamp": _epoch_to_iso(first_epoch),
        "last_data_timestamp": _epoch_to_iso(last_epoch),
        "clock_tick_us": clock_tick_us,
    }
    (DATA_DIR / "system_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def _parse_gps_epoch_seconds(time_text: str, date_text: str) -> int:
    day = int(date_text[0:2])
    month = int(date_text[2:4])
    year = 2000 + int(date_text[4:6])
    hour = int(time_text[0:2])
    minute = int(time_text[2:4])
    second = int(time_text[4:6])
    return int(datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp())


def _epoch_to_iso(epoch_seconds: int | None) -> str | None:
    if epoch_seconds is None:
        return None
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _clock_delta(current: int, previous: int) -> int:
    delta = current - previous
    if delta < 0:
        delta += 2**32
    return delta


def _cluster_clock(
    current_clock: int,
    previous_raw_clock: int | None,
    previous_cluster_clock: int | None,
) -> int:
    if previous_raw_clock is None or previous_cluster_clock is None:
        return current_clock

    delta = _clock_delta(current_clock, previous_raw_clock)
    if delta <= 1:
        return previous_cluster_clock
    return current_clock


def _write_live_rates(
    bin_counts: dict[int, list[int]],
    cumulative: list[int],
    bin_minutes: int,
    first_epoch: int | None,
    last_epoch: int | None,
) -> None:
    output = DATA_DIR / "live_rates.csv"
    bin_seconds = bin_minutes * 60
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp", "scintillator", "rate_per_min", "total_hits"],
        )
        writer.writeheader()
        for epoch_seconds in sorted(bin_counts):
            counts = bin_counts[epoch_seconds]
            timestamp = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
            duration_minutes = _bin_duration_minutes(
                epoch_seconds,
                bin_seconds,
                bin_minutes,
                first_epoch,
                last_epoch,
            )
            for index, count in enumerate(counts):
                cumulative[index] += count
                writer.writerow(
                    {
                        "timestamp": timestamp.isoformat(),
                        "scintillator": CHANNELS[index],
                        "rate_per_min": round(count / duration_minutes, 4),
                        "total_hits": cumulative[index],
                    }
                )


def _bin_duration_minutes(
    bin_epoch: int,
    bin_seconds: int,
    default_minutes: int,
    first_epoch: int | None,
    last_epoch: int | None,
) -> float:
    if first_epoch is None or last_epoch is None:
        return float(default_minutes)
    window_start = max(bin_epoch, first_epoch)
    window_end = min(bin_epoch + bin_seconds, last_epoch + 1)
    if window_end <= window_start:
        return float(default_minutes)
    return max((window_end - window_start) / 60, 1 / 60)


def _write_muon_lifetime(
    decay_times_us: list[float],
    min_decay_us: float,
    max_decay_us: float,
    bins: int = 40,
) -> None:
    output = DATA_DIR / "muon_lifetime.csv"
    if not decay_times_us:
        output.write_text("decay_time_us,counts,fit_counts\n", encoding="utf-8")
        return

    width = (max_decay_us - min_decay_us) / bins
    counts = Counter(
        min(bins - 1, max(0, int((value - min_decay_us) / width)))
        for value in decay_times_us
    )
    mean_decay = sum(decay_times_us) / len(decay_times_us)
    tau = max(mean_decay - min_decay_us, width)
    amplitude = max(counts.values())
    background = _tail_background(counts, bins)

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["decay_time_us", "counts", "fit_counts"])
        writer.writeheader()
        for index in range(bins):
            center = min_decay_us + width * (index + 0.5)
            fit = background + amplitude * math.exp(-(center - min_decay_us) / tau)
            writer.writerow(
                {
                    "decay_time_us": round(center, 4),
                    "counts": counts[index],
                    "fit_counts": round(fit, 4),
                }
            )


def _tail_background(counts: Counter[int], bins: int) -> float:
    tail_start = max(0, int(bins * 0.8))
    tail_values = [counts[index] for index in range(tail_start, bins)]
    if not tail_values:
        return 0.0
    return sum(tail_values) / len(tail_values)


def _write_absorption(
    source: Path,
    bin_counts: dict[int, list[int]],
    bin_minutes: int,
    first_epoch: int | None,
    last_epoch: int | None,
) -> None:
    output = DATA_DIR / "absorption_results.csv"
    total_counts = sum(sum(counts) for counts in bin_counts.values())
    if first_epoch is not None and last_epoch is not None and last_epoch >= first_epoch:
        total_minutes = max((last_epoch - first_epoch + 1) / 60, 1 / 60)
    else:
        total_minutes = max(len(bin_counts) * bin_minutes, 1)
    rate = total_counts / total_minutes
    uncertainty = math.sqrt(total_counts) / total_minutes if total_counts else 0.0
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["material", "thickness_cm", "rate_per_min", "uncertainty"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "material": source.stem,
                "thickness_cm": 0,
                "rate_per_min": round(rate, 4),
                "uncertainty": round(uncertainty, 4),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert detector text data into dashboard CSV files.")
    parser.add_argument("source", type=Path, help="Detector text file to convert.")
    parser.add_argument("--bin-minutes", type=int, default=10, help="Live-rate bin size.")
    parser.add_argument(
        "--clock-tick-us",
        type=float,
        default=0.024,
        help="Detector clock tick size in microseconds for muon candidate timing.",
    )
    args = parser.parse_args()

    status = parse_detector_file(
        args.source,
        bin_minutes=args.bin_minutes,
        clock_tick_us=args.clock_tick_us,
    )
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
