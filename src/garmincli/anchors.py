"""Athlete anchors from Garmin's current biometric profile: lactate threshold, FTP, VO2 max.

Pure functions, no I/O. Field names come from the Connect web app's own calls
(`/userprofile-service/biometric/current/{displayName}`, see docs/garmin-connect.openapi.json); values
that are absent, zero or implausible become None, never 0.
"""

from typing import Any, Optional

SCHEMA_VERSION = 1
LTHR_RANGE = (80, 230)  # bpm
FTP_RANGE = (30, 700)  # W
VO2_RANGE = (10, 100)  # ml/kg/min


def _entry(payload: Any, name: str) -> tuple[Optional[float], Optional[str]]:
    """A sample object `{value, bioDate}` (or a bare number) as (value, ISO date)."""
    item = payload.get(name) if isinstance(payload, dict) else None
    day = None
    if isinstance(item, dict):
        raw_day = item.get("bioDate")
        day = raw_day[:10] if isinstance(raw_day, str) and len(raw_day) >= 10 else None
        item = item.get("value")
    if isinstance(item, bool) or not isinstance(item, (int, float)) or item != item:
        return None, day
    return item, day


def _within(value: Optional[float], bounds: tuple[float, float]) -> Optional[float]:
    if value is None or not bounds[0] <= value <= bounds[1]:
        return None
    return value


def from_biometric(payload: Any) -> dict[str, Any]:
    """Anchors record from the current-biometric payload."""
    lthr, lthr_day = _entry(payload, "lactateThresholdHeartRate")
    speed, speed_day = _entry(payload, "lactateThresholdSpeed")
    ftp, _ = _entry(payload, "functionalThresholdPower")
    vo2, _ = _entry(payload, "vo2Max")
    vo2_cycling, _ = _entry(payload, "vo2MaxCycling")
    lthr = _within(lthr, LTHR_RANGE)
    speed = speed if speed is not None and speed > 0 else None
    return {
        "schemaVersion": SCHEMA_VERSION,
        "lactateThreshold": {
            "heartRateBpm": round(lthr) if lthr is not None else None,
            "date": lthr_day if lthr is not None else None,
            # Garmin's unit for this field is not documented: passed through unconverted.
            "speedRaw": speed,
            "speedDate": speed_day if speed is not None else None,
        },
        "functionalThresholdPowerW": _within(ftp, FTP_RANGE),
        "vo2MaxRunning": _within(vo2, VO2_RANGE),
        "vo2MaxCycling": _within(vo2_cycling, VO2_RANGE),
        "source": "userprofile-service/biometric/current",
        "errors": {},
    }
