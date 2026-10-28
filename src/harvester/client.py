"""Polite HTTP access to the portal.

- The contact address goes into the User-Agent, so the portal operator can reach us.
- At least `min_interval` seconds between two network requests.
- Temporary failures (timeout, connection error, HTTP 429/5xx) are retried with growing
  waits; other errors (404 ...) are not.
- Every answer is cached on disk, one JSON file per request (url, time, body). The cache
  folder is the raw data store (data/raw/): a repeated run does not ask the portal again,
  and the raw answers stay available for checking.
- Offline mode: answer only from the cache, whatever its age, and never touch the network.
"""

import hashlib
import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """A request failed for good, after the retries."""


class Client:
    def __init__(
        self,
        contact,
        *,
        cache_dir="data/raw",
        cache_ttl_hours=24.0,
        min_interval=2.0,
        attempts=4,
        timeout=30,
        session=None,
        sleep=time.sleep,
        offline=False,
    ):
        if not contact or not contact.strip():
            raise ValueError("a contact address is required for the User-Agent")
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = f"iris-sig-13-harvester/0.1 (+{contact.strip()})"
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl_hours * 3600
        self.min_interval = min_interval
        self.attempts = attempts
        self.timeout = timeout
        self.sleep = sleep
        self.offline = offline
        self._last_request = None
        self.network_requests = 0
        self.cache_hits = 0

    def get(self, url, params=None):
        """Body of a GET request as text; from the cache if a fresh copy exists."""
        full_url = requests.Request("GET", url, params=params).prepare().url
        cached = self._cache_read(full_url)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if self.offline:
            raise FetchError(f"offline: not in the cache: {full_url}")
        body = self._fetch(full_url)
        self._cache_write(full_url, body)
        return body

    # -- network ------------------------------------------------------------------

    def _fetch(self, url):
        reason = "no attempt made"
        for attempt in range(1, self.attempts + 1):
            self._pause()
            wait = 2.0**attempt  # 2, 4, 8 ... seconds
            try:
                response = self.session.get(url, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                reason = f"{type(exc).__name__}"
            else:
                self.network_requests += 1
                if response.status_code == 200:
                    response.encoding = "utf-8"  # the portal sends UTF-8; requests would guess Latin-1
                    return response.text
                reason = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUS:
                    raise FetchError(f"{reason}: {url}")
                wait = _retry_after(response) or wait
            if attempt < self.attempts:
                log.warning("attempt %d/%d failed (%s), waiting %.0f s: %s", attempt, self.attempts, reason, wait, url)
                self.sleep(wait)
        raise FetchError(f"gave up after {self.attempts} attempts ({reason}): {url}")

    def _pause(self):
        if self._last_request is not None:
            remaining = self.min_interval - (time.monotonic() - self._last_request)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request = time.monotonic()

    # -- cache --------------------------------------------------------------------

    def _cache_file(self, url):
        return self.cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")

    def _cache_read(self, url):
        if self.cache_dir is None:
            return None
        try:
            entry = json.loads(self._cache_file(url).read_text(encoding="utf-8"))
            fresh = self.offline or time.time() - entry["fetched_at"] <= self.cache_ttl  # offline: any age
            return entry["body"] if fresh and entry["url"] == url else None
        except (OSError, ValueError, KeyError):
            return None  # missing or damaged: behave as if there was no cache

    def _cache_write(self, url, body):
        if self.cache_dir is None:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            entry = {"url": url, "fetched_at": time.time(), "body": body}
            self._cache_file(url).write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write the cache: %s", exc)  # not worth failing the run for


def _retry_after(response):
    """Seconds from a numeric Retry-After header, else None."""
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None
