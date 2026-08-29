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
--manual needs a token blob (--token-blob), since connectapi.garmin.com
often blocks/rate-limits datacenter IPs (Kubernetes, VPS, ...) outright -
including plain curl requests, even with a correctly OAuth1-signed request
and a valid ticket. Re-implementing the SSO+OAuth1 handshake by hand to run
piecemeal through curl on another machine is fragile and does not reliably
route around that block.

The robust workaround is to run the *entire*, already-working login on a
machine Garmin does not block, then hand the resulting tokens over as a
single opaque string:

  1. On a machine with normal (non-datacenter) internet access:
       gc login --email you@example.com --password '...'
       gc export-token
  2. Copy the printed blob, then on this machine:
       gc login --manual --token-blob '<paste blob here>'

The blob is produced by garth's own Client.dumps()/loads() and carries both
the OAuth1 and OAuth2 tokens - treat it like a password, it grants full
account access until the tokens expire.\
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
            client.garth.loads(token_blob)
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

            if mfa_code and result and result[0]:
                # MFA was required, resume with provided code
                client_state = {"oauth1_token": result[0], "oauth2_token": result[1]}
                client.resume_login(client_state, mfa_code)

        except Exception as e:
            raise AuthenticationError(str(e)) from e

    # Save tokens
    token_dir.mkdir(parents=True, exist_ok=True)
    client.garth.dump(str(token_dir))

    return client


def export_token(tokenstore: Optional[str] = None) -> str:
    """Export the current session as a base64 token blob for --manual login elsewhere."""
    client = load_client(tokenstore=tokenstore)
    return client.garth.dumps()


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
