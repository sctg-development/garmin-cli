"""Tests for the generic `gc connect` layer (catalog coverage and placeholder resolution)."""

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from garmincli.catalog import CATALOG
from garmincli.cli import app
from garmincli.connect import (
    build_path,
    find_endpoint,
    resolve_path_values,
    usage,
)
from garmincli.errors import GarminCliError

SPEC = Path(__file__).resolve().parent.parent / "docs" / "garmin-connect.openapi.json"
TODAY = date(2026, 11, 17)


def test_every_discovered_connect_endpoint_is_in_the_catalog():
    """The OpenAPI file is the reference: nothing non-noise may be left out of the catalog."""
    spec = json.loads(SPEC.read_text())
    known = {e["path"] for e in CATALOG}
    missing = []
    for web_path, item in spec["paths"].items():
        op = item.get("get")
        if not op or op.get("x-category") == "ui-noise" or not web_path.startswith("/gc-api/"):
            continue
        path = (op.get("x-gc-api-path") or web_path[len("/gc-api"):]).rstrip("/")
        if path not in known:
            missing.append(path)
    assert missing == []


def test_catalog_names_are_unique():
    names = [e["name"] for e in CATALOG]
    assert len(names) == len(set(names))


def test_unknown_endpoint_suggests_close_matches():
    with pytest.raises(GarminCliError, match="Did you mean: hrv-daily"):
        find_endpoint("hrv-dail")


def test_display_name_is_automatic_and_dates_accept_shortcuts():
    entry = find_endpoint("hrv-daily")
    assert resolve_path_values(entry, ["2026-11-01"], lambda: "me", TODAY) == {
        "startDate": "2026-11-01",
        "endDate": "2026-11-17",
    }
    assert resolve_path_values(entry, ["week"], lambda: "me", TODAY) == {
        "startDate": "2026-11-10",
        "endDate": "2026-11-17",
    }
    named = find_endpoint("userprofile-biometric-current")
    assert resolve_path_values(named, [], lambda: "me", TODAY) == {"displayName": "me"}


def test_missing_placeholder_has_a_usage_hint():
    entry = find_endpoint("userstats-statistics-chartconfig")
    with pytest.raises(GarminCliError, match="Missing <chartId>.*Usage: connect"):
        resolve_path_values(entry, [], lambda: "me", TODAY)
    assert "<startDate> [endDate]" in usage(find_endpoint("hrv-daily"))


def test_path_values_are_url_encoded():
    entry = find_endpoint("userstats-statistics-chartconfig")
    assert build_path(entry, {"chartId": "a/b"}) == "/userstats-service/statistics/chartconfig/a%2Fb"


def test_connect_list_needs_no_login():
    result = CliRunner().invoke(app, ["connect", "list"])
    assert result.exit_code == 0
    assert "hrv-daily" in result.output
