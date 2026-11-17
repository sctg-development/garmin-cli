from typing import Any

from typer.testing import CliRunner

from garmincli import anchors as model
from garmincli.cli import app
from garmincli.commands import metrics as command

runner = CliRunner()

PAYLOAD = {
    "lactateThresholdHeartRate": {"sampleId": 1, "bioDate": "2026-04-02T08:00:00.0", "value": 164},
    "lactateThresholdSpeed": {"value": 0.35, "bioDate": "2026-04-02T08:00:00.0"},
    "functionalThresholdPower": None,
    "vo2Max": 52.0,
    "vo2MaxCycling": 0,
}


def test_reads_threshold_with_its_date_and_drops_missing_values() -> None:
    record = model.from_biometric(PAYLOAD)
    assert record["lactateThreshold"]["heartRateBpm"] == 164
    assert record["lactateThreshold"]["date"] == "2026-04-02"
    assert record["lactateThreshold"]["speedRaw"] == 0.35
    assert record["functionalThresholdPowerW"] is None
    assert record["vo2MaxRunning"] == 52.0
    assert record["vo2MaxCycling"] is None  # 0 is a "no value" marker


def test_implausible_or_malformed_values_become_null() -> None:
    record = model.from_biometric({"lactateThresholdHeartRate": {"value": 20}, "vo2Max": "x"})
    assert record["lactateThreshold"]["heartRateBpm"] is None
    assert record["lactateThreshold"]["date"] is None
    assert record["vo2MaxRunning"] is None
    assert model.from_biometric(None)["lactateThreshold"]["heartRateBpm"] is None


def test_cli_prints_the_record(monkeypatch) -> None:  # noqa: ANN001
    class _Client:
        display_name = "someone"

        def connectapi(self, path: str, **kwargs: Any) -> Any:
            assert path == "/userprofile-service/biometric/current/someone"
            return PAYLOAD

    monkeypatch.setattr(command, "load_client", lambda tokenstore=None: _Client())
    result = runner.invoke(app, ["metrics", "anchors"])
    assert result.exit_code == 0, result.stdout
    assert '"heartRateBpm": 164' in result.stdout
