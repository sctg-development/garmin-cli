"""``gc connect``: raw payload of any read-only endpoint found by the web-app discovery."""

from typing import List, Optional

import typer

from ..api import api_call
from ..auth import load_client
from ..connect import (
    CATALOG,
    build_path,
    find_endpoint,
    resolve_path_values,
    usage,
)
from ..errors import GarminCliError
from ..output import print_error, render


def _parse_params(pairs: Optional[List[str]]) -> dict[str, str]:
    """``--param key=value`` (repeatable) -> dict."""
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise GarminCliError(f"Invalid --param '{pair}': expected key=value.")
        out[key] = value
    return out


def connect(
    name: str = typer.Argument(
        ..., help="Endpoint name, or 'list' to show every endpoint."
    ),
    args: Optional[List[str]] = typer.Argument(
        None, help="Values for the path placeholders (dates accept today/yesterday/week/month)."
    ),
    param: Optional[List[str]] = typer.Option(
        None, "--param", "-p", help="Extra query parameter key=value (repeatable)."
    ),
    tokenstore: Optional[str] = typer.Option(
        None, "--tokenstore", help="Token storage path."
    ),
    fmt: str = typer.Option("json", "--format", "-f", help="Output format."),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file."),
) -> None:
    """Call a discovered Garmin Connect endpoint by name and print the raw payload."""
    try:
        if name == "list":
            rows = [
                {"name": e["name"], "usage": usage(e), "path": e["path"]}
                for e in CATALOG
            ]
            render(rows, fmt=fmt, title="Endpoints", output=output)
            return
        entry = find_endpoint(name)
        client = load_client(tokenstore=tokenstore)
        values = resolve_path_values(
            entry,
            list(args or []),
            lambda: getattr(client, "display_name", None) or _no_profile(),
        )
        data = api_call(
            client.connectapi, build_path(entry, values), params=_parse_params(param) or None
        )
        render(data, fmt=fmt, title=name, output=output)
    except GarminCliError as e:
        print_error(str(e))
        raise typer.Exit(1)


def _no_profile() -> str:
    raise GarminCliError("Garmin profile name unavailable.")
