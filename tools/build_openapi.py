#!/usr/bin/env python3
"""Build an OpenAPI 3.0.3 description of the Garmin Connect web API from discovery reports.

Input : one or more reports written by tools/discover_console.js (or discover_api.py).
Output: an OpenAPI 3.0.3 JSON document.

    python3 tools/build_openapi.py tools/run1.json -o docs/garmin-connect.openapi.json

The reports contain only shapes (key paths and types), so the schemas are *observed* shapes for one
account: every property is optional and nullable, enums and formats are not known, and a property that
was never non-empty in the capture is missing. This is a reference for exploration, not a contract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DATE_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}")
INT_QUERY = {
    "limit", "start", "maxChartSize", "maxPolylineSize", "numberOfBests", "numDaysForward",
    "nonSleepBufferMinutes", "metricId", "userId", "per-page", "pid", "likerLimit",
}
BOOL_QUERY = {
    "includeOverlaps", "includeHistory", "includeAll", "populateMealsV2", "ignorePriority", "groupByActivityType",
    "groupByParentActivityType", "groupByEventType", "standardizedUnits", "includeBiometric", "includeVO2Max",
    "ByParentType", "userFirstDay",
}
DATE_QUERY = {
    "date", "startDate", "endDate", "calendarDate", "fromDate", "untilDate", "fromCalendarDate", "toCalendarDate",
    "currentDate",
}
NOISE_SERVICES = {
    "userpreference-service", "gdprconsent-service", "consentTextServices", "myfitnesspal-service", "partner-service",
    "badge-service", "follower-service", "conversation-service", "omt-api", "golf-api", "gcs-api", "worker",
    "site-status", "web-api", "web-data", "system-service", "preference", "inbox", "snapshot",
}


def camel(text: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", text)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


class Node:
    def __init__(self) -> None:
        self.types: set[str] = set()
        self.props: dict[str, "Node"] = {}
        self.items: "Node | None" = None
        self.wild: "Node | None" = None  # dynamic keys (ids, dates) collapsed to additionalProperties

    def child(self, name: str) -> "Node":
        if name == "*":
            self.wild = self.wild or Node()
            return self.wild
        return self.props.setdefault(name, Node())

    def element(self) -> "Node":
        self.items = self.items or Node()
        return self.items


def add_fields(root: Node, fields: dict[str, list[str]]) -> None:
    for path, types in fields.items():
        node = root
        for token in path.split("."):
            arrays = 0
            while token.endswith("[]"):
                token, arrays = token[:-2], arrays + 1
            if token:
                if token.isdigit() or DATE_KEY.match(token):
                    token = "*"
                node = node.child(token)
            for _ in range(arrays):
                node.types.add("array")
                node = node.element()
        node.types.update(types)
    stack = [root]
    while stack:
        n = stack.pop()
        if n.props or n.wild:
            n.types.add("object")
        for c in [*n.props.values(), n.wild, n.items]:
            if c:
                stack.append(c)


JSON_TYPE = {"number": "number", "string": "string", "bool": "boolean", "object": "object", "array": "array"}


def schema_of(node: Node) -> dict:
    kinds = sorted(JSON_TYPE[t] for t in node.types if t in JSON_TYPE)

    def one(kind: str) -> dict:
        s: dict = {"type": kind}
        if kind == "object":
            if node.props:
                s["properties"] = {k: schema_of(v) for k, v in sorted(node.props.items())}
            if node.wild:
                s["additionalProperties"] = schema_of(node.wild)
        if kind == "array":
            s["items"] = schema_of(node.items) if node.items else {}
        return s

    schema: dict = {}
    if len(kinds) == 1:
        schema = one(kinds[0])
    elif len(kinds) > 1:
        schema = {"oneOf": [one(k) for k in kinds]}
    if "null" in node.types:
        schema["nullable"] = True
    return schema


def find_display_names(paths: list[str], explicit: list[str]) -> set[str]:
    """User names such as `Jane.D.` appear as a path segment; file names such as `x.json` do not count."""
    counts: dict[str, int] = defaultdict(int)
    for path in paths:
        for seg in set(path.split("/")):
            if "." in seg and "{" not in seg and re.fullmatch(r"[\w.\-]+", seg) and not re.search(r"\.[a-z]{2,5}$", seg):
                counts[seg] += 1
    # a user name recurs in many service paths; a preference key such as `a.b.c` appears once
    return set(explicit) | {seg for seg, n in counts.items() if n >= 5}


PARAM_BY_PREV = {"activity": "activityId", "ACTIVITY": "activityId", "chartconfig": "chartId", "weekly": "weeks", "im": "days", "preference": "preferenceKey"}


# 20+ hex digits (a GUID without dashes), possibly with `{id}` placeholders where digit runs were rewritten.
HEX_ID = re.compile(r"^(?=.*[a-f{])(?:[0-9a-f]|\{id\}){20,}$", re.IGNORECASE)


def parametrise(path: str, display_names: set[str]) -> tuple[str, list[dict]]:
    segs = path.split("/")
    # `curated/FR/<region>/<course>` (and the `/app/trails/...` page route) names a place near the account:
    # keep the shape, drop the place.
    curated = next((i for i, s in enumerate(segs) if s in ("curated", "trails")), -1)
    if curated >= 0 and re.fullmatch(r"[A-Z]{2}", segs[curated + 1] if curated + 1 < len(segs) else "") and len(segs) > curated + 3:
        segs[curated + 1 : curated + 4] = ["{country}", "{region}", "{course}"]
    dates = sum(1 for s in segs if s == "{date}")
    out, params, date_index = [], [], 0
    for i, seg in enumerate(segs):
        prev = segs[i - 1] if i else ""
        name, schema = None, {}
        if seg in display_names:
            name, schema = "displayName", {"type": "string", "description": "The account's Garmin display name (public profile name)."}
        elif seg == "{date}":
            name = "date" if dates == 1 else ("startDate", "endDate")[min(date_index, 1)]
            date_index += 1
            schema = {"type": "string", "format": "date"}
        elif seg == "{id}":
            name = "userProfileId" if prev == "daily" else PARAM_BY_PREV.get(prev, "id")
            schema = {"type": "integer", "format": "int64"}
        elif (HEX_ID.match(seg) or seg == "{guid}") and prev != "preference":
            # Garmin ids (gear, segment, challenge) are 32-digit hex strings; the discovery hook sometimes
            # rewrote the digit runs inside them as `{id}`, so those mangled forms are included.
            name, schema = "guid", {"type": "string", "format": "uuid"}
        elif prev == "preference" and seg != "{preferenceKey}":
            # Preference keys (and the opaque ids stored under /modern/preference) are account state, not
            # API surface: one placeholder for all of them.
            name, schema = "preferenceKey", {"type": "string"}
        elif seg == "{email}":
            name, schema = "email", {"type": "string"}
        elif seg.isdigit():
            before = segs[i - 2] if prev == "{date}" and i > 1 else prev
            name, schema = PARAM_BY_PREV.get(before, "n"), {"type": "integer"}
        if name:
            while any(p["name"] == name for p in params):
                name += "2"
            params.append({"name": name, "in": "path", "required": True, "schema": schema})
            out.append("{" + name + "}")
        else:
            out.append(seg)
    return "/".join(out), params


def query_param(name: str) -> dict:
    if name in DATE_QUERY:
        schema = {"type": "string", "format": "date"}
    elif name in BOOL_QUERY:
        schema = {"type": "boolean"}
    elif name in INT_QUERY:
        schema = {"type": "integer"}
    else:
        schema = {"type": "string"}
    return {"name": name, "in": "query", "required": False, "schema": schema, "description": "Observed in the web app; the value domain is not known."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=Path("openapi.json"))
    parser.add_argument("--display-name", action="append", default=[], help="a display name to redact (auto-detected otherwise)")
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()

    raw: list[dict] = []
    for report in args.reports:
        raw.extend(json.loads(report.read_text())["endpoints"])
    names = find_display_names([e["path"] for e in raw], args.display_name)

    merged: dict[tuple[str, str], dict] = {}
    for e in raw:
        template, params = parametrise(e["path"], names)
        key = (e["method"].lower(), template)
        m = merged.setdefault(key, {"params": params, "query": set(), "statuses": set(), "pages": set(), "root": Node(), "paths": set()})
        m["query"].update(e["queryParams"])
        m["statuses"].update(e["statuses"])
        for page in e["seenOn"]:
            m["pages"].add(parametrise(page, names)[0])
        m["paths"].add(e["path"])
        add_fields(m["root"], e["fields"])

    paths: dict[str, dict] = {}
    used: set[str] = set()
    for (method, template), m in sorted(merged.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        segs = [s for s in template.split("/") if s]
        skip = 1 if segs and segs[0] in ("gc-api", "golf-api", "web-api", "omt-api") else 0
        service = segs[skip] if len(segs) > skip else "root"
        if service in ("web-gateway", "modern") and len(segs) > skip + 1:
            service = segs[skip + 1]
        op = method + "".join(("By" + camel(s) if s.startswith("{") else camel(s)) for s in segs[skip:])
        base, n = op, 2
        while op in used:
            op, n = f"{base}{n}", n + 1
        used.add(op)

        schema = schema_of(m["root"])
        responses: dict = {"200": {"description": "Observed response shape.", "content": {"application/json": {"schema": schema}}}}
        for status in sorted(m["statuses"] - {200}):
            responses[str(status)] = {"description": "Observed at least once (for example, no data for that date)."}

        operation = {
            "operationId": op,
            "tags": [service],
            "summary": f"{method.upper()} {template}",
            "parameters": [*m["params"], *[query_param(q) for q in sorted(m["query"])]],
            "responses": responses,
            "x-seen-on": sorted(p for p in m["pages"] if p),
        }
        if template.startswith("/gc-api/"):
            operation["x-gc-api-path"] = template[len("/gc-api"):]
        if service in NOISE_SERVICES:
            operation["x-category"] = "ui-noise"
        paths.setdefault(template, {})[method] = operation

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Garmin Connect web API (observed, unofficial)",
            "version": args.version,
            "description": (
                "Reverse-engineered from the calls the Garmin Connect web app makes in a logged-in browser. "
                "Not published or supported by Garmin and subject to change without notice. Shapes were captured as key paths "
                "and types only (no values) for one account: all properties are optional, enums and units are unknown, and "
                "fields that were always empty during the capture are absent. Authentication was not captured and is not "
                "described here. The web app reaches these services under `/gc-api/`; `x-gc-api-path` gives the same path "
                "without that prefix, which is the form `gc api` takes (to be confirmed per endpoint). Dynamic object keys "
                "(device ids, dates) are shown as `additionalProperties`. Personal identifiers were replaced by placeholders."
            ),
        },
        "servers": [{"url": "https://connect.garmin.com", "description": "Web app host (services live under /gc-api/)"}],
        "tags": [{"name": t} for t in sorted({op["tags"][0] for item in paths.values() for op in item.values()})],
        "paths": paths,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    print(f"{sum(len(v) for v in paths.values())} operations on {len(paths)} paths -> {args.out}", file=sys.stderr)
    print(f"redacted display name(s): {len(names)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
