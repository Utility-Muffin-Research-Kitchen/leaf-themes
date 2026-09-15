"""Bounded HTTPS downloads from GitHub-owned hosts only.

Redirects are followed by hand so every hop is checked against an allowlist
and credentials are never forwarded past the first host. The body is hashed
while it streams and the download stops as soon as it passes the cap; a
Content-Length over the cap is refused before any body is read.
"""
from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

# Where a GitHub attachment or release asset download redirects to. Exact
# names only: an S3 bucket name is first come, first served, so a pattern
# like github-production-*.s3.amazonaws.com could match a bucket anyone made.
REDIRECT_HOSTS = frozenset({
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-production-repository-file-5c1aeb.s3.amazonaws.com",
    "github-production-user-asset-6210df.s3.amazonaws.com",
    "github-production-release-asset-2e65be.s3.amazonaws.com",
})
MAX_REDIRECTS = 5
CHUNK = 64 * 1024
TIMEOUT = 60
USER_AGENT = "leaf-themes-submission"


class DownloadError(Exception):
    """A download that failed or was refused. `too_large` marks the size cap."""

    def __init__(self, message: str, too_large: bool = False):
        super().__init__(message)
        self.too_large = too_large


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_fetch(url: str, headers: dict[str, str]):
    """One GET without following redirects -> (status, headers, stream)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        response = _OPENER.open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:  # 3xx and 4xx/5xx land here
        return error.code, error.headers, error
    except (urllib.error.URLError, OSError) as error:
        raise DownloadError(f"network error: {error}") from None
    return response.status, response.headers, response


def _host_ok(url: str, hosts) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname in hosts and parts.port is None \
        and parts.username is None and parts.password is None


def download(url: str, destination: str, cap: int, *, first_hosts=("github.com",),
             headers: dict[str, str] | None = None, fetch=urllib_fetch) -> tuple[int, str]:
    """Save `url` to `destination`; return (size, sha256 hex).

    `headers` (for example an Authorization header) go to the first request
    only. Every URL, including each redirect target, must be HTTPS on
    `first_hosts` (first hop) or REDIRECT_HOSTS (later hops).
    """
    if not _host_ok(url, set(first_hosts)):
        raise DownloadError("URL is not on an allowed host")
    current, extra = url, dict(headers or {})
    for hop in range(MAX_REDIRECTS + 1):
        status, response_headers, stream = fetch(current, extra)
        try:
            if status in (301, 302, 303, 307, 308):
                location = response_headers.get("Location") if response_headers else None
                if not location:
                    raise DownloadError(f"redirect {status} without a location")
                target = urljoin(current, location)
                if not _host_ok(target, REDIRECT_HOSTS):
                    raise DownloadError("redirected to a host that is not allowed")
                current, extra = target, {}
                continue
            if status != 200:
                raise DownloadError(f"HTTP {status}")
            length = response_headers.get("Content-Length") if response_headers else None
            if length is not None:
                try:
                    declared = int(length)
                except ValueError:
                    raise DownloadError("invalid Content-Length") from None
                if declared > cap:
                    raise DownloadError(f"{declared} bytes is over the {cap} byte limit",
                                        too_large=True)
            digest, size = hashlib.sha256(), 0
            with open(destination, "wb") as out:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > cap:
                        raise DownloadError(f"more than the {cap} byte limit", too_large=True)
                    digest.update(chunk)
                    out.write(chunk)
            return size, digest.hexdigest()
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
    raise DownloadError("too many redirects")
