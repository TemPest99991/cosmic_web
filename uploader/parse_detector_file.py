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
    coincidence_window_us: float = 1.0,
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
    shower_counts: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
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
                shower_counts[bin_start][sum(channel_hits)] += 1

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
    _write_shower_results(
        shower_counts,
        bin_counts,
        bin_minutes,
        first_epoch,
        last_epoch,
        coincidence_window_us,
    )

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
        "coincidence_window_us": coincidence_window_us,
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
    centers = [min_decay_us + width * (index + 0.5) for index in range(bins)]
    count_values = [counts[index] for index in range(bins)]
    fit_counts = _fit_exponential_counts(centers, count_values)

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["decay_time_us", "counts", "fit_counts"])
        writer.writeheader()
        for index in range(bins):
            writer.writerow(
                {
                    "decay_time_us": round(centers[index], 4),
                    "counts": count_values[index],
                    "fit_counts": round(fit_counts[index], 4),
                }
            )


def _fit_exponential_counts(centers: list[float], counts: list[int]) -> list[float]:
    """Fit y = background + amplitude * exp(-(x - x0) / tau) to histogram bins."""
    positive_counts = [count for count in counts if count > 0]
    if len(centers) < 3 or not positive_counts:
        return [float(count) for count in counts]

    min_positive = min(positive_counts)
    max_background = max(0.0, min_positive * 0.95)
    x0 = centers[0]
    best_fit: list[float] | None = None
    best_error: float | None = None

    # Grid-search background and lifetime, solving the best amplitude exactly
    # for each pair. This avoids SciPy while still fitting the histogram points.
    for background_step in range(0, int(max_background * 100) + 1, 25):
        background = background_step / 100
        for tau_step in range(50, 800):
            tau = tau_step / 100
            shape = [math.exp(-(center - x0) / tau) for center in centers]
            denominator = sum(value * value for value in shape)
            if denominator <= 0:
                continue
            amplitude = max(
                sum(value * (count - background) for value, count in zip(shape, counts))
                / denominator,
                0.0,
            )
            fit = [background + amplitude * value for value in shape]
            error = sum((count - fitted) ** 2 for count, fitted in zip(counts, fit))
            if best_error is None or error < best_error:
                best_error = error
                best_fit = fit

    return best_fit if best_fit is not None else [float(count) for count in counts]


def _write_shower_results(
    shower_counts: dict[int, list[int]],
    bin_counts: dict[int, list[int]],
    bin_minutes: int,
    first_epoch: int | None,
    last_epoch: int | None,
    coincidence_window_us: float,
) -> None:
    output = DATA_DIR / "shower_results.csv"
    bin_seconds = bin_minutes * 60
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "multiplicity",
                "observed_count",
                "rate_per_min",
                "random_expected_count",
            ],
        )
        writer.writeheader()
        for epoch_seconds in sorted(shower_counts):
            timestamp = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
            duration_minutes = _bin_duration_minutes(
                epoch_seconds,
                bin_seconds,
                bin_minutes,
                first_epoch,
                last_epoch,
            )
            duration_seconds = duration_minutes * 60
            random_expected = _random_coincidence_estimate(
                bin_counts[epoch_seconds],
                duration_seconds,
                coincidence_window_us,
            )
            for multiplicity in (2, 3, 4):
                count = shower_counts[epoch_seconds][multiplicity]
                writer.writerow(
                    {
                        "timestamp": timestamp.isoformat(),
                        "multiplicity": multiplicity,
                        "observed_count": count,
                        "rate_per_min": round(count / duration_minutes, 4),
                        "random_expected_count": round(random_expected[multiplicity], 8),
                    }
                )


def _random_coincidence_estimate(
    channel_counts: list[int],
    duration_seconds: float,
    coincidence_window_us: float,
) -> dict[int, float]:
    if duration_seconds <= 0 or coincidence_window_us <= 0:
        return {2: 0.0, 3: 0.0, 4: 0.0}

    window_seconds = coincidence_window_us * 1e-6
    windows = duration_seconds / window_seconds
    probabilities = [
        min(max(count / windows, 0.0), 1.0)
        for count in channel_counts
    ]

    expected = {2: 0.0, 3: 0.0, 4: 0.0}
    for mask in range(1, 1 << len(probabilities)):
        multiplicity = mask.bit_count()
        if multiplicity < 2:
            continue
        probability = 1.0
        for index, channel_probability in enumerate(probabilities):
            if mask & (1 << index):
                probability *= channel_probability
            else:
                probability *= 1 - channel_probability
        expected[multiplicity] += windows * probability
    return expected


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
    parser.add_argument(
        "--coincidence-window-us",
        type=float,
        default=1.0,
        help="Coincidence window used for random shower-candidate estimates.",
    )
    args = parser.parse_args()

    status = parse_detector_file(
        args.source,
        bin_minutes=args.bin_minutes,
        clock_tick_us=args.clock_tick_us,
        coincidence_window_us=args.coincidence_window_us,
    )
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
