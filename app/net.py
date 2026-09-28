"""
How this app makes HTTPS connections: one TLS context, verified the way the
operating system verifies, and a readable reason when verification fails.

**Why not Python's default.** `ssl.create_default_context()` on Windows reads
a *snapshot* of the Windows certificate store. Windows doesn't ship every root
certificate; it fetches one the first time the *system* verifier needs it —
which browsers trigger and Python never does. So on a machine that has seldom
browsed (a pit laptop), GitHub's root may simply not be there yet, and Python
fails with CERTIFICATE_VERIFY_FAILED while Edge on the same machine is fine.
That is exactly what happened on the first pit machine (2026-09-24): updates
worked at home, failed on the deploy machine, "same certificate".

`truststore` hands verification to the OS itself (Windows CryptoAPI, macOS
Security framework), so it fetches missing roots on demand and trusts what an
organisation installed — including a school network's filter that re-signs
HTTPS — exactly as the browser does. pip uses it for the same reason. If it is
missing for any reason, this falls back to Python's default rather than to
no verification: verification is never turned off here.

Qt's own networking (the relay WebSocket) already uses the OS verifier
(schannel on Windows) and needs none of this.
"""

from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.parse

_context: ssl.SSLContext | None = None
_verifier = ""


def ssl_context() -> ssl.SSLContext:
    """The shared client context. Built once; never raises."""
    global _context, _verifier
    if _context is None:
        try:
            import truststore
            _context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            _verifier = "the operating system's own verifier (truststore)"
        except Exception:
            _context = ssl.create_default_context()
            _verifier = "Python's bundled verifier (truststore unavailable)"
    return _context


def verifier() -> str:
    ssl_context()
    return _verifier


def tls_failure(exc: BaseException) -> ssl.SSLError | None:
    """The TLS error inside `exc` (a URLError usually wraps it), or None."""
    reason = getattr(exc, "reason", exc)
    return reason if isinstance(reason, ssl.SSLError) else None


def explain_tls(exc: BaseException, host: str) -> str:
    """
    One operator sentence for a failed certificate check, naming the likely
    cause. The raw text is kept at the end, because it's what to search for.
    """
    err = tls_failure(exc) or exc
    raw = str(getattr(err, "verify_message", "") or err)
    low = raw.lower()
    clock = time.strftime("%Y-%m-%d %H:%M")
    if "expired" in low or "not yet valid" in low or "time" in low:
        cause = (f"it looks expired or not yet valid, which almost always "
                 f"means this machine's clock is wrong (it says {clock}). Fix "
                 "the date and time in Windows settings and try again.")
    elif "self-signed" in low or "self signed" in low or "untrusted root" in low \
            or "not trusted" in low:
        cause = ("something on this network is re-signing secure traffic — a "
                 "school or venue web filter usually. Try another network (a "
                 "phone hotspot); if that works, the filter needs to allow "
                 f"{host}.")
    elif "local issuer" in low or "unable to get" in low:
        cause = ("this machine doesn't have the certificate authority that "
                 f"vouches for {host}. Opening https://{host} once in Edge "
                 "usually makes Windows fetch it; if not, a network filter may "
                 "be re-signing traffic — try a phone hotspot.")
    else:
        cause = (f"the secure connection to {host} was refused. Check the "
                 f"clock (it says {clock}) and try another network.")
    return f"Couldn't verify {host}'s certificate: {cause} ({raw})"


def describe_url_error(exc: urllib.error.URLError, host: str, what: str) -> str:
    """A URLError as one sentence: TLS failures explained, the rest as-is."""
    if tls_failure(exc) is not None:
        return explain_tls(exc, host)
    return f"Could not reach {what}: {exc.reason}. This machine may have no internet."


# ── `--net-check`: the diagnosis, run on the machine that's failing ──────────

CHECK_URLS = (
    ("GitHub (updates)", "https://api.github.com/"),
    ("frc.nexus (event feed)", "https://frc.nexus/api/v1/events"),
    ("Nexus relay", "https://nexus.bh-stack.com/healthz"),
    ("Sync hub", "https://sync.bh-stack.com/healthz"),
)


def _try(url: str, context: ssl.SSLContext) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "breakaway-pit-display"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=context) as r:
            return f"ok (HTTP {r.status})"
    except urllib.error.HTTPError as e:
        return f"ok (reached; HTTP {e.code})"   # TLS succeeded to get a status at all
    except urllib.error.URLError as e:
        host = urllib.parse.urlparse(url).netloc
        return ("FAILED: " + explain_tls(e, host)) if tls_failure(e) else f"FAILED: {e.reason}"
    except Exception as e:
        return f"FAILED: {type(e).__name__}: {e}"


def check() -> tuple[bool, list[str]]:
    """Every endpoint, with the verifier the app uses and with Python's default."""
    lines = [f"clock       {time.strftime('%Y-%m-%d %H:%M:%S %Z')}  (a wrong date breaks every certificate)",
             f"verifier    {verifier()}"]
    ok = True
    default = ssl.create_default_context()
    for name, url in CHECK_URLS:
        mine = _try(url, ssl_context())
        plain = _try(url, default)
        ok &= mine.startswith("ok")
        lines.append(f"{name}")
        lines.append(f"  app        {mine}")
        if plain != mine:
            lines.append(f"  python     {plain}   <- what builds before 2026-09-24 saw")
    return ok, lines
