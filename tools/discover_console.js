/*
 * Discover the Garmin Connect web API from your own, already logged-in browser (no Playwright, no
 * login automation: Cloudflare Turnstile blocks automated browsers, not you).
 *
 * How to use
 *   1. Open https://connect.garmin.com/modern/ and log in as usual.
 *   2. Open DevTools (Cmd+Option+I) > Console, paste this whole file, press Enter.
 *      (Chrome may ask you to type "allow pasting" first.)
 *   3. Browse the pages you want to explore *inside the app* (click, don't reload: a reload removes the
 *      hook; if that happens, paste the file again, what was captured so far is kept for the tab).
 *      Suggestions: Sleep, HRV status, Health Status, Training Readiness, Training Status,
 *      Endurance Score, Stress, Body Battery, Respiration, Pulse Ox, an activity, Reports > Health.
 *   4. In the console run:   copy(garminDiscoveryReport())
 *      and paste the clipboard into a file (e.g. ~/discovery.json).
 *
 * What it records, and what it never records
 *   Records: method, status, URL path (dates, ids, GUIDs, e-mails replaced by placeholders), the NAMES
 *   of query parameters, and the SHAPE of the JSON response (key paths and value types).
 *   Never records: any value, header, cookie, token, request body or response body.
 *   It only reads; it sends nothing anywhere. Remove it with a page reload.
 *   Read the report before you share it: a path may still contain something personal.
 */
(() => {
  const STORE = "garminDiscovery.v1";
  const MAX_DEPTH = 7, MAX_PATHS = 500;
  let data = {};
  try { data = JSON.parse(sessionStorage.getItem(STORE) || "{}"); } catch (_) { data = {}; }

  const norm = (p) => p
    .replace(/[^/]+@[^/]+/g, "{email}")
    .replace(/[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}/gi, "{guid}")
    .replace(/\d{4}-\d{2}-\d{2}/g, "{date}")
    .replace(/(?<![A-Za-z])\d{5,}(?![A-Za-z])/g, "{id}");

  const typeOf = (v) => v === null ? "null" : Array.isArray(v) ? "array" : typeof v === "object" ? "object"
    : typeof v === "number" ? "number" : typeof v === "boolean" ? "bool" : "string";

  const shape = (v, prefix, depth, out) => {
    if (Object.keys(out).length >= MAX_PATHS || depth >= MAX_DEPTH) return;
    if (Array.isArray(v)) { v.slice(0, 20).forEach((c) => shape(c, prefix + "[]", depth + 1, out)); }
    else if (v && typeof v === "object") {
      for (const [k, c] of Object.entries(v)) {
        const name = prefix ? prefix + "." + k : k;
        (out[name] = out[name] || []);
        const t = typeOf(c);
        if (!out[name].includes(t)) out[name].push(t);
        shape(c, name, depth + 1, out);
      }
    }
  };

  const record = (method, rawUrl, status, json) => {
    let u;
    try { u = new URL(rawUrl, location.href); } catch (_) { return; }
    if (!u.hostname.endsWith("garmin.com")) return;
    const path = norm(u.pathname), key = method + " " + path;
    const e = (data[key] = data[key] || { method, host: u.hostname, path, statuses: [], queryParams: [], seenOn: [], fields: {} });
    if (!e.statuses.includes(status)) e.statuses.push(status);
    for (const q of u.searchParams.keys()) if (!e.queryParams.includes(q)) e.queryParams.push(q);
    const page = norm(location.pathname);
    if (!e.seenOn.includes(page)) e.seenOn.push(page);
    if (json !== undefined) {
      const found = {};
      shape(json, "", 0, found);
      for (const [name, types] of Object.entries(found)) {
        const have = (e.fields[name] = e.fields[name] || []);
        types.forEach((t) => { if (!have.includes(t)) have.push(t); });
      }
    }
    try { sessionStorage.setItem(STORE, JSON.stringify(data)); } catch (_) { /* full: keep going in memory */ }
  };

  if (!window.__garminDiscoveryHooked) {
    window.__garminDiscoveryHooked = true;

    const realFetch = window.fetch;
    window.fetch = function (input, init) {
      const method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
      const url = typeof input === "string" ? input : input && input.url ? input.url : String(input);
      return realFetch.apply(this, arguments).then((res) => {
        try {
          if ((res.headers.get("content-type") || "").includes("json")) {
            res.clone().json().then((j) => record(method, url, res.status, j), () => record(method, url, res.status));
          }
        } catch (_) { /* never break the page */ }
        return res;
      });
    };

    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.addEventListener("load", () => {
        try {
          if ((this.getResponseHeader("content-type") || "").includes("json")) {
            let j; try { j = JSON.parse(this.responseText); } catch (_) { /* not JSON after all */ }
            record(String(method).toUpperCase(), url, this.status, j);
          }
        } catch (_) { /* never break the page */ }
      });
      return open.apply(this, arguments);
    };
  }

  window.garminDiscoveryReport = () => JSON.stringify(
    { capturedAt: new Date().toISOString(), endpoints: Object.values(data).sort((a, b) => a.path.localeCompare(b.path)) }, null, 2);
  window.garminDiscoveryReset = () => { data = {}; sessionStorage.removeItem(STORE); };
  console.log("Garmin discovery hook active. " + Object.keys(data).length + " endpoint(s) already captured. " +
    "Browse inside the app, then run: copy(garminDiscoveryReport())");
})();
