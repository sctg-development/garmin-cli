"""Garmin API wrapper with error handling."""

from typing import Any, Callable, Optional

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .errors import AuthenticationError, ConnectionError, GarminCliError, RateLimitError


def api_call(func: Callable, *args: Any, **kwargs: Any) -> Any:
    """Execute a Garmin API call with standardized error handling."""
    try:
        return func(*args, **kwargs)
    except GarminConnectAuthenticationError as e:
        raise AuthenticationError(
            f"Authentication failed. Try 'gc login' again. ({e})"
        ) from e
    except GarminConnectTooManyRequestsError as e:
        raise RateLimitError(
            f"Rate limit exceeded. Wait a moment and try again. ({e})"
        ) from e
    except GarminConnectConnectionError as e:
        raise ConnectionError(f"Connection error: {e}") from e
    except Exception as e:
        raise GarminCliError(f"Unexpected error: {e}") from e


def raw_connectapi_call(
    client: Any,
    path: str,
    method: str = "GET",
    params: Optional[dict] = None,
    payload: Any = None,
) -> Any:
    """Call an arbitrary Garmin Connect API path/method with the underlying client.

    `garminconnect`'s own `connectapi()` helper is GET-only (both the current
    curl_cffi-based client and the older garth-based one), so writes go
    through the lower-level `request()` method instead, which supports any
    HTTP method.
    """
    backend = getattr(client, "client", None) or getattr(client, "garth", None)
    if backend is None:
        raise GarminCliError("Garmin client exposes no underlying API backend.")

    method = method.upper()
    if method == "GET":
        return backend.connectapi(path, params=params)

    response = backend.request(
        method, "connectapi", path, api=True, params=params, json=payload
    )
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    if hasattr(response, "json"):
        try:
            return response.json()
        except Exception:
            return getattr(response, "text", response)
    return response
