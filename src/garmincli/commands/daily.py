"""`gc daily`: one compact physiological record per day."""

from datetime import timedelta
from typing import Any, Callable, Optional

import typer

from ..api import api_call
from ..auth import load_client
from ..daily import (
    SCHEMA_VERSION,
    apply_hrv_summary,
    apply_sleep_detail,
    apply_sleep_stats_row,
    apply_user_summary,
    empty_record,
    key_paths,
)
from ..dates import fmt as fmt_date
from ..dates import parse_date, resolve_date
from ..errors import (
    AuthenticationError,
    ConnectionError,
    GarminCliError,
    RateLimitError,
)
from ..output import print_error, render

MAX_RANGE_DAYS = 400
# Above this number of days, per-day detail calls are refused: they cost 3 requests per day.
MAX_DETAIL_DAYS = 14


def _error_code(error: GarminCliError) -> str:
    if isinstance(error, AuthenticationError):
        return "auth"
    if isinstance(error, RateLimitError):
        return "rate_limited"
    if isinstance(error, ConnectionError):
        return "connection"
    return "error"


def _days(start: str, end: str) -> list[str]:
    first, last = parse_date(start), parse_date(end)
    return [fmt_date(first + timedelta(days=i)) for i in range((last - first).days + 1)]


def _try(errors: dict[str, str], name: str, call: Callable[[], Any]) -> Any:
    """Run one Garmin call; a failure is recorded under `name` and returns None."""
    try:
        return call()
    except AuthenticationError:
        # Nothing else can succeed without a session.
        raise
    except GarminCliError as error:
        errors[name] = _error_code(error)
        return None


def _sleep_stats(client: Any, start: str, end: str) -> Any:
    """Daily sleep statistics; raw endpoint on garminconnect versions without the method."""
    method = getattr(client, "get_sleep_daily", None)
    if method is not None:
        return method(start, end)
    rows: list[Any] = []
    seen: set[str] = set()
    first, last = parse_date(start), parse_date(end)
    while first <= last:
        chunk_end = min(first + timedelta(days=27), last)
        data = client.connectapi(
            f"/sleep-service/stats/sleep/daily/{fmt_date(first)}/{fmt_date(chunk_end)}"
        )
        for row in (data or {}).get("individualStats") or []:
            day = row.get("calendarDate")
            if day and day not in seen:
                seen.add(day)
                rows.append(row)
        first = chunk_end + timedelta(days=1)
    return rows


def _hrv_range(client: Any, start: str, end: str) -> Any:
    method = getattr(client, "get_hrv_data_range", None)
    if method is not None:
        return method(start, end)
    return client.connectapi(f"/hrv-service/hrv/daily/{start}/{end}")


def _resting_hr_range(client: Any, start: str, end: str) -> Any:
    method = getattr(client, "get_rhr_daily", None)
    if method is not None:
        return method(start, end)
    name = getattr(client, "display_name", None)
    if not name:
        raise GarminCliError("Garmin profile name unavailable.")
    data = client.connectapi(
        f"/userstats-service/wellness/daily/{name}",
        params={"fromDate": start, "untilDate": end, "metricId": 60},
    )
    rows = ((data or {}).get("allMetrics") or {}).get("metricsMap", {}).get(
        "WELLNESS_RESTING_HEART_RATE"
    ) or []
    return [
        {"calendarDate": row.get("calendarDate"), "value": row.get("value")}
        for row in rows
        if row.get("value") is not None
    ]


def collect(
    client: Any,
    start: str,
    end: str,
    detail: bool,
    probe: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the records for [start, end]. `probe` receives raw payloads when given."""
    days = _days(start, end)
    records = {day: empty_record(day) for day in days}
    errors: dict[str, str] = {}

    def remember(name: str, payload: Any) -> None:
        if probe is not None and name not in probe and payload:
            probe[name] = payload

    sleep_rows = _try(
        errors, "sleepStats", lambda: api_call(_sleep_stats, client, start, end)
    )
    remember("sleepStats", sleep_rows)
    for row in sleep_rows or []:
        day = row.get("calendarDate") if isinstance(row, dict) else None
        if day in records:
            apply_sleep_stats_row(records[day], row)
            records[day]["sources"].append("sleepStats")

    hrv_range = _try(
        errors, "hrvRange", lambda: api_call(_hrv_range, client, start, end)
    )
    remember("hrvRange", hrv_range)
    summaries = hrv_range.get("hrvSummaries") if isinstance(hrv_range, dict) else None
    for summary in summaries or []:
        day = summary.get("calendarDate") if isinstance(summary, dict) else None
        if day in records:
            apply_hrv_summary(records[day], summary)
            records[day]["sources"].append("hrvRange")

    rhr_rows = _try(
        errors,
        "restingHrRange",
        lambda: api_call(_resting_hr_range, client, start, end),
    )
    remember("restingHrRange", rhr_rows)
    for row in rhr_rows or []:
        day = row.get("calendarDate") if isinstance(row, dict) else None
        value = row.get("value") if isinstance(row, dict) else None
        if day in records and isinstance(value, (int, float)) and value > 0:
            # The wellness statistic is Garmin's reference resting heart rate.
            records[day]["restingHr"] = value
            records[day]["sources"].append("restingHrRange")

    if detail:
        for day in days:
            record = records[day]
            day_errors: dict[str, str] = record["errors"]
            sleep = _try(
                day_errors, "sleep", lambda d=day: api_call(client.get_sleep_data, d)
            )
            remember("sleep", sleep)
            if sleep:
                apply_sleep_detail(record, sleep)
                record["sources"].append("sleep")
            hrv = _try(
                day_errors, "hrv", lambda d=day: api_call(client.get_hrv_data, d)
            )
            remember("hrv", hrv)
            if isinstance(hrv, dict):
                apply_hrv_summary(record, hrv.get("hrvSummary"))
                record["sources"].append("hrv")
            summary = _try(
                day_errors,
                "summary",
                lambda d=day: api_call(client.get_user_summary, d),
            )
            remember("summary", summary)
            if summary:
                apply_user_summary(record, summary)
                record["sources"].append("summary")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "start": start,
        "end": end,
        "detail": detail,
        "days": [records[day] for day in days],
        "errors": errors,
    }


def daily(
    date_shortcut: Optional[str] = typer.Argument(
        None, help="Date shortcut or YYYY-MM-DD."
    ),
    date: Optional[str] = typer.Option(None, "--date", "-d", help="Date (YYYY-MM-DD)."),
    start: Optional[str] = typer.Option(None, "--start", help="Start date."),
    end: Optional[str] = typer.Option(None, "--end", help="End date."),
    detail: Optional[bool] = typer.Option(
        None,
        "--detail/--no-detail",
        help=(
            "Also fetch the per-day sleep, HRV and summary payloads (3 requests per day): "
            "adds stress, body battery, SpO2 and respiration detail. "
            f"Default: on for ranges up to {MAX_DETAIL_DAYS} days."
        ),
    ),
    keys: bool = typer.Option(
        False,
        "--keys",
        help="Print the key paths of the raw Garmin payloads instead of records (no values).",
    ),
    tokenstore: Optional[str] = typer.Option(
        None, "--tokenstore", help="Token storage path."
    ),
    fmt: str = typer.Option(
        "json", "--format", "-f", help="Output format (json/table)."
    ),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file."),
) -> None:
    """Compact per-day recovery signals: sleep, HRV, resting HR, respiration, SpO2, skin temperature, stress.

    Uses Garmin's range endpoints (a few requests for a whole month), so it is
    the cheap way to build a history. All fields are nullable: a missing value
    is null, never 0.
    """
    try:
        first, last = resolve_date(date_shortcut, date, start, end)
        last = last or first
        span = (parse_date(last) - parse_date(first)).days + 1
        if span < 1:
            raise GarminCliError("--end is before --start.")
        if span > MAX_RANGE_DAYS:
            raise GarminCliError(f"Range too long: {span} days (max {MAX_RANGE_DAYS}).")
        if detail is None:
            detail = span <= MAX_DETAIL_DAYS
        elif detail and span > MAX_DETAIL_DAYS:
            raise GarminCliError(
                f"--detail is limited to {MAX_DETAIL_DAYS} days (3 requests per day); got {span}."
            )

        client = load_client(tokenstore=tokenstore)
        probe: Optional[dict[str, Any]] = {} if keys else None
        data = collect(client, first, last, detail, probe)
        if probe is not None:
            data = {name: key_paths(payload) for name, payload in probe.items()}
        render(data, fmt=fmt, title=f"Daily ({first} to {last})", output=output)
    except GarminCliError as e:
        print_error(str(e))
        raise typer.Exit(1)
