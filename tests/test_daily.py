from typing import Any

from typer.testing import CliRunner

from garmincli import daily as model
from garmincli.cli import app
from garmincli.commands import daily as command
from garmincli.errors import AuthenticationError, RateLimitError

runner = CliRunner()

# 2025-01-14 22:30:00 UTC and 2025-01-15 06:00:00 UTC, in milliseconds.
START_MS = 1736893800000
END_MS = 1736920800000

STATS_ROW = {
    "calendarDate": "2025-01-15",
    "values": {
        "totalSleepTimeInSeconds": 25200,
        "deepTime": 5400,
        "lightTime": 14400,
        "remTime": 5400,
        "awakeTime": 1800,
        "sleepScore": 82,
        "gmtSleepStartTimeInMillis": START_MS,
        "gmtSleepEndTimeInMillis": END_MS,
        "localSleepStartTimeInMillis": START_MS + 3600000,
        "localSleepEndTimeInMillis": END_MS + 3600000,
        "restingHeartRate": 47,
        "avgOvernightHrv": 62.0,
        "hrv7dAverage": 60.0,
        "hrvStatus": "BALANCED",
        "respiration": 13.5,
        "spO2": 95.0,
        "skinTempC": -0.3,
        "bodyBatteryChange": 61,
        "sleepNeed": 480,
    },
}

SLEEP_DETAIL = {
    "dailySleepDTO": {
        "sleepTimeSeconds": 25200,
        "deepSleepSeconds": 5400,
        "lightSleepSeconds": 14400,
        "remSleepSeconds": 5400,
        "awakeSleepSeconds": 1800,
        "sleepStartTimestampGMT": START_MS,
        "sleepEndTimestampGMT": END_MS,
        "sleepStartTimestampLocal": START_MS + 3600000,
        "sleepEndTimestampLocal": END_MS + 3600000,
        "averageSpO2Value": 94.0,
        "lowestSpO2Value": 88,
        "averageRespirationValue": 13.0,
        "lowestRespirationValue": 11.0,
        "highestRespirationValue": 17.0,
        "avgSleepStress": 18.0,
        "awakeCount": 2,
        "sleepScores": {"overall": {"value": 80}},
        "sleepNeed": {"baseline": 480, "actual": 500},
    },
    "restingHeartRate": 48,
    "avgOvernightHrv": 61.0,
    "hrvStatus": "BALANCED",
    "bodyBatteryChange": 60,
    "avgSkinTempDeviationC": 0.4,
}


def test_stats_row_fills_every_recovery_signal() -> None:
    record = model.empty_record("2025-01-15")
    model.apply_sleep_stats_row(record, STATS_ROW)

    assert record["sleep"]["durationS"] == 25200
    assert record["sleep"]["startGmt"] == "2025-01-14T22:30:00Z"
    assert record["sleep"]["startLocal"] == "2025-01-14T23:30:00"
    assert record["sleep"]["score"] == 82
    assert record["sleep"]["needS"] == 480 * 60
    assert record["hrv"]["lastNightAvgMs"] == 62.0
    assert record["restingHr"] == 47
    assert record["respiration"]["sleepAvg"] == 13.5
    assert record["spo2"]["sleepAvg"] == 95.0
    assert record["skinTempDeviationC"] == -0.3
    assert record["bodyBattery"]["sleepChange"] == 61


def test_detail_reads_the_nightly_dto_and_keeps_missing_values_null() -> None:
    record = model.empty_record("2025-01-15")
    model.apply_sleep_detail(record, SLEEP_DETAIL)

    assert record["sleep"]["needS"] == 500 * 60
    assert record["sleep"]["awakeCount"] == 2
    assert record["spo2"] == {"sleepAvg": 94.0, "sleepLow": 88, "dayAvg": None, "dayLow": None}
    assert record["respiration"]["sleepHigh"] == 17.0
    assert record["skinTempDeviationC"] == 0.4
    assert record["stress"] is None


def test_a_day_without_sleep_stays_null_not_zero() -> None:
    record = model.empty_record("2025-01-15")
    model.apply_sleep_detail(record, {"dailySleepDTO": {"sleepTimeSeconds": None, "userProfilePK": 1}})
    model.apply_user_summary(record, {"restingHeartRate": -1, "averageStressLevel": -1, "totalSteps": 0})

    assert record["sleep"] is None
    assert record["restingHr"] is None
    assert record["stress"] is None
    assert record["activity"]["steps"] == 0


def test_fahrenheit_deviation_converts_without_offset() -> None:
    record = model.empty_record("2025-01-15")
    model.apply_sleep_stats_row(record, {"values": {"skinTempF": 0.9, "totalSleepTimeInSeconds": 1}})
    assert record["skinTempDeviationC"] == 0.5


def test_hrv_service_overrides_the_sleep_copy_and_adds_the_baseline() -> None:
    record = model.empty_record("2025-01-15")
    model.apply_sleep_stats_row(record, STATS_ROW)
    model.apply_hrv_summary(
        record,
        {
            "lastNightAvg": 64,
            "lastNight5MinHigh": 90,
            "weeklyAvg": 61,
            "status": "BALANCED",
            "baseline": {"lowUpper": 50, "balancedLow": 55, "balancedUpper": 70},
        },
    )
    assert record["hrv"]["lastNightAvgMs"] == 64
    assert record["hrv"]["baselineBalancedLowMs"] == 55


def test_key_paths_never_contain_values() -> None:
    paths = model.key_paths({"a": {"b": 61, "c": [{"d": "secret"}]}, "e": []})
    assert paths == ["a.b", "a.c[].d", "e[]"]


class _Client:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_sleep_daily(self, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append("sleep_daily")
        return [STATS_ROW]

    def get_hrv_data_range(self, start: str, end: str) -> dict[str, Any]:
        self.calls.append("hrv_range")
        return {"hrvSummaries": [{"calendarDate": "2025-01-15", "lastNightAvg": 64, "status": "BALANCED"}]}

    def get_rhr_daily(self, start: str, end: str) -> list[dict[str, Any]]:
        self.calls.append("rhr_daily")
        raise RateLimitError("slow down")

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        self.calls.append("sleep")
        return SLEEP_DETAIL

    def get_hrv_data(self, day: str) -> None:
        self.calls.append("hrv")
        return None

    def get_user_summary(self, day: str) -> dict[str, Any]:
        self.calls.append("summary")
        return {"averageStressLevel": 31, "bodyBatteryHighestValue": 88, "averageSpo2": 96}


def _passthrough(func, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    return func(*args, **kwargs)


def test_collect_range_isolates_a_failing_signal(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(command, "api_call", _passthrough)
    client = _Client()
    data = command.collect(client, "2025-01-14", "2025-01-15", detail=False)

    assert client.calls == ["sleep_daily", "hrv_range", "rhr_daily"]
    assert data["errors"] == {"restingHrRange": "rate_limited"}
    assert [d["date"] for d in data["days"]] == ["2025-01-14", "2025-01-15"]
    assert data["days"][0]["sleep"] is None
    assert data["days"][1]["hrv"]["lastNightAvgMs"] == 64
    assert data["days"][1]["restingHr"] == 47


def test_collect_detail_adds_the_daily_summary(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(command, "api_call", _passthrough)
    data = command.collect(_Client(), "2025-01-15", "2025-01-15", detail=True)
    day = data["days"][0]

    assert day["stress"]["avg"] == 31
    assert day["bodyBattery"] == {"high": 88, "low": None, "charged": None, "drained": None, "sleepChange": 61}
    assert day["spo2"]["dayAvg"] == 96
    assert day["spo2"]["sleepLow"] == 88
    assert "summary" in day["sources"]


def test_cli_refuses_detail_on_a_long_range(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(command, "load_client", lambda tokenstore=None: _Client())
    result = runner.invoke(app, ["daily", "--start", "2025-01-01", "--end", "2025-03-01", "--detail"])
    assert result.exit_code == 1


def test_cli_keys_mode_prints_paths_only(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(command, "load_client", lambda tokenstore=None: _Client())
    monkeypatch.setattr(command, "api_call", _passthrough)
    result = runner.invoke(app, ["daily", "2025-01-15", "--keys"])

    assert result.exit_code == 0
    assert "values.avgOvernightHrv" in result.stdout
    assert "62.0" not in result.stdout


def test_cli_stops_on_authentication_failure(monkeypatch) -> None:  # noqa: ANN001
    class _NoSession(_Client):
        def get_sleep_daily(self, start: str, end: str) -> list[dict[str, Any]]:
            raise AuthenticationError("expired")

    monkeypatch.setattr(command, "load_client", lambda tokenstore=None: _NoSession())
    monkeypatch.setattr(command, "api_call", _passthrough)
    assert runner.invoke(app, ["daily", "2025-01-15"]).exit_code == 1


def test_old_garminconnect_without_range_methods_uses_raw_endpoints(monkeypatch) -> None:  # noqa: ANN001
    class _Old:
        display_name = "someone"

        def __init__(self) -> None:
            self.paths: list[str] = []

        def connectapi(self, path: str, **kwargs: Any) -> Any:
            self.paths.append(path)
            if path.startswith("/sleep-service"):
                return {"individualStats": [STATS_ROW]}
            if path.startswith("/hrv-service"):
                return {"hrvSummaries": [{"calendarDate": "2025-01-15", "lastNightAvg": 64}]}
            assert kwargs["params"]["metricId"] == 60
            return {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"calendarDate": "2025-01-15", "value": 46}]}}}

    monkeypatch.setattr(command, "api_call", _passthrough)
    client = _Old()
    data = command.collect(client, "2025-01-15", "2025-01-15", detail=False)

    assert data["errors"] == {}
    assert data["days"][0]["restingHr"] == 46
    assert data["days"][0]["hrv"]["lastNightAvgMs"] == 64
    assert data["days"][0]["sleep"]["durationS"] == 25200
    assert client.paths[0] == "/sleep-service/stats/sleep/daily/2025-01-15/2025-01-15"
