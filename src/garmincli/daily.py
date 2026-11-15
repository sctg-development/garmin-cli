"""Compact per-day physiological records built from Garmin Connect payloads.

`gc daily` exists for programs that need a history of recovery signals (HRV,
resting heart rate, sleep, respiration, SpO2, skin temperature, stress) without
downloading one large payload per signal and per day. This module holds the
pure part: turning raw Garmin payloads into one small, stable record per day.

Rules:
- Garmin field names differ between endpoints, devices and firmware. Every
  field is looked up through a list of candidate names, and a missing field is
  `None`, never 0.
- Units are fixed by the record (seconds, ms, bpm, breaths/min, %, deg C).
- The record never contains intraday series: only nightly/daily aggregates.
"""

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

# Garmin uses negative numbers (-1, -2) as "no value" markers in several payloads.
_NO_VALUE_MAX = 0


def _flat(row: Any) -> dict[str, Any]:
    """Return a dict view of a payload row, lifting a nested `values` object."""
    if not isinstance(row, dict):
        return {}
    values = row.get("values")
    if isinstance(values, dict):
        return {**row, **values}
    return row


def _pick(source: Any, names: Iterable[str]) -> Any:
    """First non-null value among candidate keys."""
    if not isinstance(source, dict):
        return None
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return None


def _num(source: Any, *names: str, positive: bool = True) -> Optional[float]:
    """Numeric field, or None. With `positive`, Garmin's <=0 markers are dropped."""
    value = _pick(source, names)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    if positive and value <= _NO_VALUE_MAX:
        return None
    return value


def _signed(source: Any, *names: str) -> Optional[float]:
    return _num(source, *names, positive=False)


def _text(source: Any, *names: str) -> Optional[str]:
    value = _pick(source, names)
    return value if isinstance(value, str) and value else None


def _iso_from_ms(value: Any, as_utc: bool) -> Optional[str]:
    """Epoch milliseconds to ISO 8601.

    Garmin's "local" timestamps are the local wall-clock time encoded as if it
    were UTC, so they are rendered without a zone suffix.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return text + "Z" if as_utc else text


def _drop_empty(section: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A section whose fields are all None is reported as None."""
    return section if any(v is not None for v in section.values()) else None


def empty_record(day: str) -> dict[str, Any]:
    return {
        "date": day,
        "sleep": None,
        "hrv": None,
        "restingHr": None,
        "respiration": None,
        "spo2": None,
        "skinTempDeviationC": None,
        "stress": None,
        "bodyBattery": None,
        "activity": None,
        "heartRate": None,
        "sources": [],
        "errors": {},
    }


def _merge(record: dict[str, Any], key: str, section: Optional[dict[str, Any]]) -> None:
    """Merge a section into the record; existing non-null fields win."""
    if section is None:
        return
    current = record.get(key)
    if not isinstance(current, dict):
        record[key] = section
        return
    for name, value in section.items():
        if current.get(name) is None:
            current[name] = value


def _sleep_need_seconds(source: dict[str, Any]) -> Optional[float]:
    """Garmin's personal sleep need, reported in minutes (object or number)."""
    need = source.get("sleepNeed")
    minutes = (
        _num(need, "actual", "baseline")
        if isinstance(need, dict)
        else _num(source, "sleepNeed")
    )
    return minutes * 60 if minutes is not None else None


def _sleep_score(source: dict[str, Any]) -> Optional[float]:
    scores = source.get("sleepScores")
    if isinstance(scores, dict):
        overall = scores.get("overall")
        value = _num(overall, "value") if isinstance(overall, dict) else None
        if value is not None:
            return value
    return _num(source, "sleepScore", "overallSleepScore")


def _skin_temp(source: dict[str, Any]) -> Optional[float]:
    celsius = _signed(
        source, "avgSkinTempDeviationC", "skinTempDeviationC", "skinTempC"
    )
    if celsius is not None:
        return round(celsius, 2)
    fahrenheit = _signed(
        source, "avgSkinTempDeviationF", "skinTempDeviationF", "skinTempF"
    )
    # A deviation converts with the scale factor only (no 32 degree offset).
    return round(fahrenheit * 5 / 9, 2) if fahrenheit is not None else None


def apply_sleep_stats_row(record: dict[str, Any], row: Any) -> None:
    """Merge one row of the sleep statistics range endpoint (`get_sleep_daily`)."""
    src = _flat(row)
    if not src:
        return
    _merge(
        record,
        "sleep",
        _drop_empty(
            {
                "startGmt": _iso_from_ms(
                    _pick(src, ["gmtSleepStartTimeInMillis", "sleepStartTimestampGMT"]),
                    True,
                ),
                "endGmt": _iso_from_ms(
                    _pick(src, ["gmtSleepEndTimeInMillis", "sleepEndTimestampGMT"]),
                    True,
                ),
                "startLocal": _iso_from_ms(
                    _pick(
                        src, ["localSleepStartTimeInMillis", "sleepStartTimestampLocal"]
                    ),
                    False,
                ),
                "endLocal": _iso_from_ms(
                    _pick(src, ["localSleepEndTimeInMillis", "sleepEndTimestampLocal"]),
                    False,
                ),
                "durationS": _num(src, "totalSleepTimeInSeconds", "sleepTimeSeconds"),
                "deepS": _num(src, "deepTime", "deepSleepSeconds", positive=False),
                "lightS": _num(src, "lightTime", "lightSleepSeconds", positive=False),
                "remS": _num(src, "remTime", "remSleepSeconds", positive=False),
                "awakeS": _num(src, "awakeTime", "awakeSleepSeconds", positive=False),
                "napS": _num(src, "napTimeSeconds", positive=False),
                "awakeCount": _num(src, "awakeCount", positive=False),
                "score": _sleep_score(src),
                "needS": _sleep_need_seconds(src),
                "avgStress": _num(src, "avgSleepStress"),
                "avgHr": _num(src, "avgHeartRate"),
            }
        ),
    )
    _merge(
        record,
        "hrv",
        _drop_empty(
            {
                "lastNightAvgMs": _num(src, "avgOvernightHrv"),
                "lastNight5MinHighMs": None,
                "weeklyAvgMs": _num(src, "hrv7dAverage"),
                "status": _text(src, "hrvStatus"),
                "baselineLowUpperMs": None,
                "baselineBalancedLowMs": None,
                "baselineBalancedUpperMs": None,
            }
        ),
    )
    if record.get("restingHr") is None:
        record["restingHr"] = _num(src, "restingHeartRate")
    _merge(
        record,
        "respiration",
        _drop_empty(
            {
                "sleepAvg": _num(
                    src, "respiration", "averageRespirationValue", "averageRespiration"
                ),
                "sleepLow": _num(src, "lowestRespirationValue"),
                "sleepHigh": _num(src, "highestRespirationValue"),
                "wakingAvg": None,
            }
        ),
    )
    _merge(
        record,
        "spo2",
        _drop_empty(
            {
                "sleepAvg": _num(src, "spO2", "averageSpO2Value", "averageSPO2"),
                "sleepLow": _num(src, "lowestSpO2Value", "lowestSPO2"),
                "dayAvg": None,
                "dayLow": None,
            }
        ),
    )
    if record.get("skinTempDeviationC") is None:
        record["skinTempDeviationC"] = _skin_temp(src)
    _merge(
        record,
        "bodyBattery",
        _drop_empty(
            {
                "high": None,
                "low": None,
                "charged": None,
                "drained": None,
                "sleepChange": _signed(src, "bodyBatteryChange"),
            }
        ),
    )


def apply_sleep_detail(record: dict[str, Any], payload: Any) -> None:
    """Merge the single-night sleep payload (`get_sleep_data`)."""
    if not isinstance(payload, dict):
        return
    dto = payload.get("dailySleepDTO")
    # The nightly DTO and the top level carry the same kind of fields as a stats row.
    merged = {**(dto if isinstance(dto, dict) else {})}
    for key in (
        "restingHeartRate",
        "avgOvernightHrv",
        "hrvStatus",
        "bodyBatteryChange",
        "avgSkinTempDeviationC",
        "avgSkinTempDeviationF",
    ):
        if payload.get(key) is not None and merged.get(key) is None:
            merged[key] = payload[key]
    # A day without recorded sleep still returns a DTO, with null timestamps.
    if (
        _num(merged, "sleepTimeSeconds") is None
        and merged.get("sleepStartTimestampGMT") is None
    ):
        return
    apply_sleep_stats_row(record, merged)


def apply_hrv_summary(record: dict[str, Any], summary: Any) -> None:
    """Merge one HRV summary (the `hrvSummary` of a day, or a range row)."""
    if not isinstance(summary, dict):
        return
    baseline = (
        summary.get("baseline") if isinstance(summary.get("baseline"), dict) else {}
    )
    section = _drop_empty(
        {
            "lastNightAvgMs": _num(summary, "lastNightAvg"),
            "lastNight5MinHighMs": _num(summary, "lastNight5MinHigh"),
            "weeklyAvgMs": _num(summary, "weeklyAvg"),
            "status": _text(summary, "status"),
            "baselineLowUpperMs": _num(baseline, "lowUpper"),
            "baselineBalancedLowMs": _num(baseline, "balancedLow"),
            "baselineBalancedUpperMs": _num(baseline, "balancedUpper"),
        }
    )
    if section is None:
        return
    current = record.get("hrv")
    if isinstance(current, dict):
        # The HRV service is the reference: it overrides the copy found in sleep stats.
        for name, value in section.items():
            if value is not None:
                current[name] = value
    else:
        record["hrv"] = section


def apply_user_summary(record: dict[str, Any], payload: Any) -> None:
    """Merge the daily user summary (`get_user_summary`)."""
    if not isinstance(payload, dict):
        return
    if record.get("restingHr") is None:
        record["restingHr"] = _num(payload, "restingHeartRate")
    _merge(
        record,
        "stress",
        _drop_empty(
            {
                "avg": _num(payload, "averageStressLevel"),
                "max": _num(payload, "maxStressLevel"),
                "restS": _num(payload, "restStressDuration", positive=False),
                "lowS": _num(payload, "lowStressDuration", positive=False),
                "mediumS": _num(payload, "mediumStressDuration", positive=False),
                "highS": _num(payload, "highStressDuration", positive=False),
            }
        ),
    )
    _merge(
        record,
        "bodyBattery",
        _drop_empty(
            {
                "high": _num(payload, "bodyBatteryHighestValue"),
                "low": _num(payload, "bodyBatteryLowestValue"),
                "charged": _num(payload, "bodyBatteryChargedValue", positive=False),
                "drained": _num(payload, "bodyBatteryDrainedValue", positive=False),
                "sleepChange": None,
            }
        ),
    )
    _merge(
        record,
        "respiration",
        _drop_empty(
            {
                "sleepAvg": _num(payload, "avgSleepRespirationValue"),
                "sleepLow": None,
                "sleepHigh": None,
                "wakingAvg": _num(payload, "avgWakingRespirationValue"),
            }
        ),
    )
    _merge(
        record,
        "spo2",
        _drop_empty(
            {
                "sleepAvg": None,
                "sleepLow": None,
                "dayAvg": _num(payload, "averageSpo2"),
                "dayLow": _num(payload, "lowestSpo2"),
            }
        ),
    )
    _merge(
        record,
        "activity",
        _drop_empty(
            {
                "steps": _num(payload, "totalSteps", positive=False),
                "activeKcal": _num(payload, "activeKilocalories", positive=False),
                "bmrKcal": _num(payload, "bmrKilocalories"),
                "moderateMin": _num(
                    payload, "moderateIntensityMinutes", positive=False
                ),
                "vigorousMin": _num(
                    payload, "vigorousIntensityMinutes", positive=False
                ),
                "floorsUp": _num(payload, "floorsAscended", positive=False),
            }
        ),
    )
    _merge(
        record,
        "heartRate",
        _drop_empty(
            {
                "min": _num(payload, "minHeartRate"),
                "max": _num(payload, "maxHeartRate"),
            }
        ),
    )


def key_paths(payload: Any, prefix: str = "", depth: int = 4) -> list[str]:
    """Key paths of a payload, without any value: a privacy-safe contract probe."""
    if depth == 0 and isinstance(payload, (dict, list)):
        return [prefix + "…"] if prefix else []
    if isinstance(payload, dict):
        paths: list[str] = []
        for key in sorted(payload):
            child = f"{prefix}.{key}" if prefix else str(key)
            nested = key_paths(payload[key], child, depth - 1)
            paths.extend(nested or [child])
        return paths
    if isinstance(payload, list):
        return (
            key_paths(payload[0], prefix + "[]", depth - 1)
            if payload
            else [prefix + "[]"]
        )
    return [prefix] if prefix else []
