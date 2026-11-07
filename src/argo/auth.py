"""Who is making the request.

In production every request comes through Identity-Aware Proxy (IAP), which
adds a signed JWT in the ``x-goog-iap-jwt-assertion`` header. We verify that
signature against Google's public keys and use the signed-in Google account's
email as the user id, so each person gets their own conversations and files.
The plain ``X-Goog-Authenticated-User-Email`` header is deliberately ignored:
anything that reaches the pod without going through IAP could forge it.

Locally there is no IAP, so every request is the single local user.

Settings (environment):
    ARGO_AUTH      "iap" to require the IAP header (set in k8s/configmap.yaml).
                   Unset: local mode.
    IAP_AUDIENCE   Optional. The expected ``aud`` claim,
                   /projects/<PROJECT_NUMBER>/global/backendServices/<SERVICE_ID>.
                   When set, tokens IAP issued for other apps are rejected too.
    LOCAL_USER_ID  Local mode only. Defaults to "aseem", the id earlier
                   versions used for everyone, so existing local chats stay visible.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

from fastapi import HTTPException, Request
from google.auth import exceptions as auth_exceptions
from google.auth import jwt

log = logging.getLogger("argo.auth")

IAP_HEADER = "x-goog-iap-jwt-assertion"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_KEYS_URL = "https://www.gstatic.com/iap/verify/public_key"
KEYS_TTL = 3600  # seconds; Google rotates these keys rarely

_keys: dict[str, str] = {}
_keys_fetched = 0.0
_keys_lock = threading.Lock()


def _iap_keys(kid: str | None) -> dict[str, str]:
    """IAP's public keys ({key id: PEM}), refetched hourly or on an unknown key id."""
    global _keys, _keys_fetched
    with _keys_lock:
        stale = time.monotonic() - _keys_fetched > KEYS_TTL
        if stale or (kid and kid not in _keys):
            with urllib.request.urlopen(IAP_KEYS_URL, timeout=10) as resp:
                _keys = json.load(resp)
            _keys_fetched = time.monotonic()
        return _keys


def _verify_iap(token: str) -> str:
    """The verified email from an IAP JWT; raises HTTP 401 if anything is off."""
    try:
        kid = jwt.decode_header(token).get("kid")
        claims = jwt.decode(
            token,
            certs=_iap_keys(kid),
            audience=os.environ.get("IAP_AUDIENCE") or None,
            clock_skew_in_seconds=30,
        )
    except (auth_exceptions.GoogleAuthError, ValueError) as exc:
        log.warning("rejected IAP token: %s", exc)
        raise HTTPException(401, "Not signed in") from None
    if claims.get("iss") != IAP_ISSUER:
        raise HTTPException(401, "Not signed in")
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        raise HTTPException(401, "Not signed in")
    return email.strip().lower()


def current_user(request: Request) -> str:
    """FastAPI dependency: the user id for this request.

    A plain (sync) function on purpose: FastAPI runs it in a worker thread, so
    the occasional key fetch doesn't block the event loop.
    """
    if os.environ.get("ARGO_AUTH", "").lower() != "iap":
        return os.environ.get("LOCAL_USER_ID") or "aseem"
    token = request.headers.get(IAP_HEADER)
    if not token:
        raise HTTPException(401, "Not signed in")
    return _verify_iap(token)
