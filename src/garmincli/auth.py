"""Authentication and token management."""

import os
import shutil
from pathlib import Path
from typing import Optional

from garminconnect import Garmin

from .errors import AuthenticationError


def get_token_dir(tokenstore: Optional[str] = None) -> Path:
    """Resolve the token storage directory.

    Priority:
    1. Explicit --tokenstore argument
    2. GARMINTOKENS environment variable
    3. Fallback: ~/.config/garmin-cli/tokens/
    """
    if tokenstore:
        return Path(tokenstore).expanduser().resolve()

    env = os.environ.get("GARMINTOKENS")
    if env:
        return Path(env).expanduser().resolve()

    return Path.home() / ".config" / "garmin-cli" / "tokens"


MANUAL_LOGIN_HELP = """\
--manual needs a token blob (--token-blob). garminconnect's own login()
already runs a cascade of login strategies (native mobile, SSO embed
widget, portal web - each with TLS-fingerprint impersonation and, where
needed, an anti-WAF delay) and should succeed directly with 'gc login' in
most environments, including datacenter/Kubernetes ones. Fall back to
--manual only if every strategy is rate-limited (HTTP 429) for this
account/network right now:

  1. On a machine/network where 'gc login' succeeds:
       gc login --email you@example.com --password '...'
       gc export-token
  2. Copy the printed blob, then on this machine:
       gc login --manual --token-blob '<paste blob here>'

The blob is produced by garminconnect's own token dump/load format and
carries full account access until the tokens expire - treat it like a
password.\
"""


def login(
    email: Optional[str],
    password: Optional[str],
    mfa_code: Optional[str] = None,
    wait_mfa: bool = False,
    manual: bool = False,
    tokenstore: Optional[str] = None,
    token_blob: Optional[str] = None,
) -> Garmin:
    """Authenticate with Garmin Connect and save tokens."""
    token_dir = get_token_dir(tokenstore)

    if manual:
        if not token_blob:
            raise AuthenticationError(MANUAL_LOGIN_HELP)
        client = Garmin()
        try:
            client.client.loads(token_blob)
        except Exception as e:
            raise AuthenticationError(f"Could not load token blob: {e}") from e
    else:
        if not email or not password:
            raise AuthenticationError("--email and --password are required.")
        if wait_mfa:
            prompt_mfa = lambda: input("Enter MFA code: ")  # noqa: E731
            client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        elif mfa_code:
            client = Garmin(email=email, password=password, return_on_mfa=True)
        else:
            client = Garmin(email=email, password=password)

        try:
            result = client.login()

            if mfa_code and result and result[0] == "needs_mfa":
                # MFA was required, resume with the provided code. MFA state is
                # kept on `client` itself, not in the (unused) client_state arg.
                client.resume_login({}, mfa_code)

        except Exception as e:
            raise AuthenticationError(str(e)) from e

    # Save tokens
    token_dir.mkdir(parents=True, exist_ok=True)
    client.client.dump(str(token_dir))

    return client


def export_token(tokenstore: Optional[str] = None) -> str:
    """Export the current session as a token blob for --manual login elsewhere."""
    client = load_client(tokenstore=tokenstore)
    return client.client.dumps()


def load_client(tokenstore: Optional[str] = None) -> Garmin:
    """Load a Garmin client from saved tokens."""
    token_dir = get_token_dir(tokenstore)

    if not token_dir.exists():
        raise AuthenticationError("Not logged in. Run 'gc login' first.")

    client = Garmin()
    try:
        client.login(tokenstore=str(token_dir))
    except FileNotFoundError:
        raise AuthenticationError("Token files not found. Run 'gc login' first.")
    except Exception as e:
        raise AuthenticationError(f"Failed to load session: {e}") from e

    return client


def logout(tokenstore: Optional[str] = None) -> None:
    """Remove saved tokens."""
    token_dir = get_token_dir(tokenstore)
    if token_dir.exists():
        shutil.rmtree(token_dir)
