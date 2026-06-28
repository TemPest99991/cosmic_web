from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def append_demo_update() -> None:
    """Append one synthetic detector update so the live dashboard visibly changes."""
    DATA_DIR.mkdir(exist_ok=True)
    output = DATA_DIR / "live_rates.csv"
    now = datetime.now(timezone.utc).replace(microsecond=0)

    if output.exists():
        history = pd.read_csv(output)
    else:
        history = pd.DataFrame(columns=["timestamp", "scintillator", "rate_per_min", "total_hits"])

    latest = (
        history.sort_values("timestamp")
        .groupby("scintillator", as_index=False)
        .tail(1)
        if not history.empty
        else pd.DataFrame()
    )

    rows = []
    for idx in range(1, 5):
        name = f"Scintillator {idx}"
        previous = latest.loc[latest["scintillator"] == name]
        previous_total = int(previous["total_hits"].iloc[0]) if not previous.empty else 10000 + idx * 500
        rate = 38 + idx * 1.7 + (now.second % 9) - 4
        rows.append(
            {
                "timestamp": now.isoformat(),
                "scintillator": name,
                "rate_per_min": round(rate, 2),
                "total_hits": previous_total + int(rate / 2),
            }
        )

    combined = pd.concat([history, pd.DataFrame(rows)], ignore_index=True)
    combined.to_csv(output, index=False)
    write_system_status("demo_update", malformed_records=0)


def import_rates_csv(source: Path) -> None:
    """Copy a prepared rate CSV into the dashboard data folder."""
    required = {"timestamp", "scintillator", "rate_per_min", "total_hits"}
    rates = pd.read_csv(source)
    missing = required - set(rates.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ValueError(f"Rate CSV is missing columns: {missing_text}")

    DATA_DIR.mkdir(exist_ok=True)
    rates.to_csv(DATA_DIR / "live_rates.csv", index=False)
    write_system_status(str(source), malformed_records=0)


def write_system_status(current_log_file: str, malformed_records: int) -> None:
    status = {
        "current_log_file": current_log_file,
        "malformed_records": malformed_records,
        "last_upload": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    (DATA_DIR / "system_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update dashboard data files.")
    parser.add_argument("--demo", action="store_true", help="Append one synthetic live-rate update.")
    parser.add_argument("--rates-csv", type=Path, help="Import a prepared live-rates CSV.")
    args = parser.parse_args()

    if args.demo:
        append_demo_update()
        return

    if args.rates_csv:
        import_rates_csv(args.rates_csv)
        return

    parser.error("Choose --demo or --rates-csv.")


if __name__ == "__main__":
    main()
