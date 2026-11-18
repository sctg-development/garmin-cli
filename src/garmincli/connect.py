"""Generic access to every read-only endpoint found by the web-app discovery.

The catalog (``catalog.py``) is generated from ``docs/garmin-connect.openapi.json`` by
``garmin-ts-client/tools/build_catalog.ts`` and is shared with the TypeScript client, so both tools expose
the same endpoint names. This module holds the pure logic (name lookup, placeholder resolution, path
building); the ``gc connect`` command in ``commands/connect.py`` only does I/O.
"""

import re
from datetime import date
from typing import Any, Callable, Optional
from urllib.parse import quote

from .catalog import CATALOG
from .dates import SHORTCUTS, fmt, parse_date
from .errors import GarminCliError

_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def endpoint_names() -> list[str]:
    """Names of the catalogued endpoints, sorted."""
    return [entry["name"] for entry in CATALOG]


def find_endpoint(name: str) -> dict[str, Any]:
    """Return the catalog entry called ``name``.

    Raises:
        GarminCliError: when the name is unknown; close matches are suggested.
    """
    for entry in CATALOG:
        if entry["name"] == name:
            return entry
    near = [e["name"] for e in CATALOG if name in e["name"]][:5]
    hint = f" Did you mean: {', '.join(near)}?" if near else " Run 'gc connect list'."
    raise GarminCliError(f"Unknown endpoint '{name}'.{hint}")


def usage(entry: dict[str, Any]) -> str:
    """One-line usage string, e.g. ``connect hrv-daily <startDate> [endDate]``."""
    args = [
        f"[{p}]" if p == "endDate" else f"<{p}>"
        for p in entry["params"]
        if p != "displayName"
    ]
    query = (
        f" [--param {'=… | '.join(entry['query'])}=…]" if entry["query"] else ""
    )
    return f"Usage: connect {entry['name']}{' ' + ' '.join(args) if args else ''}{query}"


def _is_date_param(name: str) -> bool:
    return name.lower().endswith("date")


def _resolve_date(value: str, today: date) -> tuple[str, Optional[str]]:
    """``today``/``yesterday``/``week``/``month`` or YYYY-MM-DD -> (start, end or None)."""
    if value in SHORTCUTS:
        start, end = SHORTCUTS[value]()
        # SHORTCUTS reads the real clock; shift by the injected "today" so tests are deterministic.
        offset = today - date.today()
        start = start + offset
        end = end + offset if end else None
        return fmt(start), fmt(end) if end else None
    return fmt(parse_date(value)), None


def resolve_path_values(
    entry: dict[str, Any],
    positional: list[str],
    display_name: Callable[[], str],
    today: Optional[date] = None,
) -> dict[str, str]:
    """Turn the caller's positional arguments into path values.

    - ``displayName`` is never positional: ``display_name()`` supplies it (called lazily).
    - Date placeholders accept the usual shortcuts or YYYY-MM-DD.
    - For a ``{startDate}/{endDate}`` pair a range shortcut (week, month) fills both; a single date
      fills ``startDate`` and ``endDate`` defaults to today.
    - Every other placeholder (ids, counts) is taken verbatim.

    Raises:
        GarminCliError: when a required placeholder has no value.
    """
    today = today or date.today()
    queue = list(positional)
    values: dict[str, str] = {}
    for name in entry["params"]:
        if name == "displayName":
            values[name] = display_name()
        elif _is_date_param(name):
            given = queue.pop(0) if queue else None
            if given is None and name == "endDate":
                values[name] = fmt(today)
                continue
            if given is None:
                raise GarminCliError(f"Missing <{name}>. {usage(entry)}")
            start, end = _resolve_date(given, today)
            values[name] = start
            # A range shortcut also supplies the end of the pair: hand it to the next placeholder.
            if name == "startDate" and end:
                queue.insert(0, end)
        else:
            if not queue:
                raise GarminCliError(f"Missing <{name}>. {usage(entry)}")
            values[name] = queue.pop(0)
    return values


def build_path(entry: dict[str, Any], values: dict[str, str]) -> str:
    """Fill the placeholders of ``entry['path']`` (values are URL-encoded)."""
    return _PLACEHOLDER.sub(lambda m: quote(values[m.group(1)], safe=""), entry["path"])
