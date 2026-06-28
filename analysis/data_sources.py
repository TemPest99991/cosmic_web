from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class DashboardData:
    rate_history: pd.DataFrame
    muon_lifetime: pd.DataFrame
    absorption: pd.DataFrame
    angular: pd.DataFrame
    source_name: str
    current_log_file: str | None = None
    malformed_records: int = 0
    last_upload: datetime | None = None

    @property
    def last_update(self) -> datetime | None:
        if self.rate_history.empty:
            return None
        timestamps = pd.to_datetime(self.rate_history["timestamp"], utc=True, errors="coerce")
        latest = timestamps.max()
        if pd.isna(latest):
            return None
        return latest.to_pydatetime()

    @property
    def total_triggers(self) -> int:
        if self.rate_history.empty or "total_hits" not in self.rate_history:
            return 0
        latest = self._latest_rate_rows()
        return int(latest["total_hits"].sum())

    @property
    def overall_rate(self) -> float:
        if self.rate_history.empty or "rate_per_min" not in self.rate_history:
            return 0.0
        latest = self._latest_rate_rows()
        return float(latest["rate_per_min"].sum())

    @property
    def recording_duration(self) -> timedelta | None:
        if self.rate_history.empty:
            return None
        timestamps = pd.to_datetime(self.rate_history["timestamp"], utc=True, errors="coerce")
        timestamps = timestamps.dropna()
        if timestamps.empty:
            return None
        return timestamps.max().to_pydatetime() - timestamps.min().to_pydatetime()

    @property
    def current_rates(self) -> pd.DataFrame:
        return self._latest_rate_rows()

    def _latest_rate_rows(self) -> pd.DataFrame:
        if self.rate_history.empty:
            return pd.DataFrame(columns=["timestamp", "scintillator", "rate_per_min", "total_hits"])
        rates = self.rate_history.copy()
        rates["timestamp"] = pd.to_datetime(rates["timestamp"], utc=True, errors="coerce")
        rates = rates.dropna(subset=["timestamp"])
        if rates.empty:
            return pd.DataFrame(columns=["timestamp", "scintillator", "rate_per_min", "total_hits"])
        return (
            rates.sort_values("timestamp")
            .groupby("scintillator", as_index=False)
            .tail(1)
            .sort_values("scintillator")
            .reset_index(drop=True)
        )


def load_dashboard_data(use_sample_fallback: bool = True) -> DashboardData:
    """Load dashboard data from CSV files, with generated sample data as a fallback."""
    rate_history = _read_csv("live_rates.csv")
    muon_lifetime = _read_csv("muon_lifetime.csv")
    absorption = _read_csv("absorption_results.csv")
    angular = _read_csv("angular_results.csv")
    system = _read_json("system_status.json")

    source_name = "Local CSV files"
    if use_sample_fallback:
        samples = sample_dashboard_data()
        if rate_history.empty:
            rate_history = samples.rate_history
            source_name = "Sample data"
        if muon_lifetime.empty:
            muon_lifetime = samples.muon_lifetime
        if absorption.empty:
            absorption = samples.absorption
        if angular.empty:
            angular = samples.angular

    return DashboardData(
        rate_history=rate_history,
        muon_lifetime=muon_lifetime,
        absorption=absorption,
        angular=angular,
        source_name=source_name,
        current_log_file=system.get("current_log_file"),
        malformed_records=int(system.get("malformed_records", 0)),
        last_upload=_parse_datetime(system.get("last_upload")),
    )


def sample_dashboard_data() -> DashboardData:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    times = [now - timedelta(minutes=5 * step) for step in range(36, -1, -1)]
    rows: list[dict[str, Any]] = []
    detector_offsets = [0.0, -2.4, -5.7, -8.1]

    for detector_index, offset in enumerate(detector_offsets, start=1):
        total = 12000 + detector_index * 550
        for idx, timestamp in enumerate(times):
            rate = 42 + offset + 4 * np.sin(idx / 4 + detector_index) + 1.5 * np.cos(idx / 7)
            rate = max(rate, 2)
            total += int(rate * 5)
            rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "scintillator": f"Scintillator {detector_index}",
                    "rate_per_min": round(rate, 2),
                    "total_hits": total,
                }
            )

    decay_time = np.linspace(0.2, 10.0, 28)
    fit_counts = 180 * np.exp(-decay_time / 2.2) + 7
    counts = np.maximum(np.round(fit_counts + 10 * np.sin(decay_time * 1.7)), 0).astype(int)
    lifetime = pd.DataFrame(
        {
            "decay_time_us": decay_time.round(2),
            "counts": counts,
            "fit_counts": fit_counts.round(2),
        }
    )

    absorption = pd.DataFrame(
        {
            "material": ["None", "Lead", "Lead", "Lead", "Aluminum", "Aluminum", "Aluminum"],
            "thickness_cm": [0, 1, 2, 4, 1, 3, 6],
            "rate_per_min": [42.4, 38.2, 34.9, 28.7, 40.1, 36.5, 31.4],
            "uncertainty": [1.1, 1.0, 1.0, 0.9, 1.1, 1.0, 0.9],
        }
    )

    angles = np.array([0, 10, 20, 30, 40, 50, 60, 70, 80])
    cos2 = 40 * np.cos(np.deg2rad(angles)) ** 2
    angular = pd.DataFrame(
        {
            "angle_deg": angles,
            "corrected_rate": (cos2 + np.array([1.0, -0.8, 0.6, -1.2, 0.7, -0.6, 0.4, -0.5, 0.2])).round(2),
            "cos2_fit": cos2.round(2),
        }
    )

    return DashboardData(
        rate_history=pd.DataFrame(rows),
        muon_lifetime=lifetime,
        absorption=absorption,
        angular=angular,
        source_name="Sample data",
        current_log_file="sample_detector_run.txt",
        malformed_records=0,
        last_upload=now,
    )


def _read_csv(filename: str) -> pd.DataFrame:
    path = DATA_DIR / filename
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _read_json(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    return pd.read_json(path, typ="series").to_dict()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()
