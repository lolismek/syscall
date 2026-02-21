from __future__ import annotations

import math
import statistics
from typing import Iterable


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]

    idx = (len(sorted_values) - 1) * (pct / 100.0)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_values[lo]
    weight = idx - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def summarize(samples_us: Iterable[float]) -> tuple[float, float, float, float, float]:
    values = [float(v) for v in samples_us]
    if not values:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    sorted_values = sorted(values)
    median = statistics.median(sorted_values)
    p95 = percentile(sorted_values, 95.0)
    mean = statistics.fmean(sorted_values)
    stdev = statistics.stdev(sorted_values) if len(sorted_values) >= 2 else 0.0
    cov = stdev / mean if mean else 0.0
    return (median, p95, mean, stdev, cov)
