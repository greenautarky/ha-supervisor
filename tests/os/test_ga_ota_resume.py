"""GA fork (Odoo #2136, SV-2): the OTA bundle download is resumable and verified.

The download runs against a REAL aiohttp server (Range, ETag/If-Range, cuts,
404/503, no-range mode) through a REAL ClientSession - the HTTP behaviour is
the thing under test, so it is not mocked. Only the backoff constants are
shrunk so a test does not wait.
"""

# pylint: disable=protected-access

import hashlib
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, web
import pytest

from supervisor.coresys import CoreSys
from supervisor.exceptions import HassOSUpdateError
from supervisor.os import manager as os_manager

NAME = "haos_ihost-18.4.1.7.raucb"
SIZE = 3 * 1024 * 1024 + 123


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    """The OTA store: one bundle, its sidecar, and fault injection."""

    def __init__(self) -> None:
        """Start with an empty store and no faults."""
        self.files: dict[str, bytes] = {}
        self.etag = '"build-1"'
        self.cut_after: int | None = None  # body bytes before the connection drops
        self.cut_count = 0
        self.fail_status: list[int] = []  # statuses to answer first, in order
        self.missing: set[str] = set()
        self.no_ranges = False
        self.log: list[dict] = []
        self.url = ""

    def put(self, data: bytes, sidecar: str | None = "auto") -> None:
        """Publish the bundle and (by default) a matching sidecar."""
        self.files[NAME] = data
        if sidecar == "auto":
            self.files[f"{NAME}.sha256"] = f"{_sha(data)}  {NAME}\n".encode()
        elif sidecar is not None:
            self.files[f"{NAME}.sha256"] = sidecar.encode()
        else:
            self.files.pop(f"{NAME}.sha256", None)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Serve a file with Range/If-Range and the configured faults."""
        name = request.match_info["name"]
        rng = request.headers.get("Range")
        if_range = request.headers.get("If-Range")
        entry = {"name": name, "range": rng, "if_range": if_range, "sent": 0}
        self.log.append(entry)
        if name.endswith(".raucb") and self.fail_status:
            status = self.fail_status.pop(0)
            entry["status"] = status
            return web.Response(status=status)
        if name in self.missing or name not in self.files:
            entry["status"] = 404
            return web.Response(status=404)
        data = self.files[name]
        start = 0
        status = 200
        if rng and not self.no_ranges and (if_range is None or if_range == self.etag):
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                entry["status"] = 416
                return web.Response(
                    status=416, headers={"Content-Range": f"bytes */{len(data)}"}
                )
            status = 206
        body = data[start:]
        headers = {"ETag": self.etag, "Content-Length": str(len(body))}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{len(data) - 1}/{len(data)}"
        resp = web.StreamResponse(status=status, headers=headers)
        await resp.prepare(request)
        entry["status"] = status
        limit = None
        if name.endswith(".raucb") and self.cut_count > 0:
            self.cut_count -= 1
            limit = self.cut_after
        chunk = 64 * 1024
        pos = 0
        while pos < len(body):
            piece = body[pos : pos + chunk]
            if limit is not None and pos + len(piece) > limit:
                piece = piece[: max(0, limit - pos)]
                if piece:
                    await resp.write(piece)
                    entry["sent"] += len(piece)
                request.transport.close()
                return resp
            await resp.write(piece)
            entry["sent"] += len(piece)
            pos += len(piece)
        await resp.write_eof()
        return resp

    def bundle_requests(self) -> list[dict]:
        """Return the logged requests for the bundle itself."""
        return [e for e in self.log if e["name"] == NAME]


@pytest.fixture(name="no_backoff")
def fixture_no_backoff():
    """Do not wait between attempts."""
    with patch.object(os_manager, "OTA_BACKOFF", 0):
        yield


@pytest.fixture(name="store")
async def fixture_store(aiohttp_server, coresys: CoreSys):
    """Run a real OTA store server and use a real client session."""
    st = Store()
    app = web.Application()
    app.router.add_get("/ota/{name}", st.handle)
    server = await aiohttp_server(app)
    session = ClientSession()
    coresys._websession = session  # noqa: SLF001 - a REAL session, not a mock
    st.url = str(server.make_url(f"/ota/{NAME}"))
    yield st
    await session.close()


def test_ota_constants_are_pinned():
    """Production values (pinned literals, rule 7)."""
    assert os_manager.OTA_STALL_TIMEOUT == 300
    assert os_manager.OTA_TOTAL_TIMEOUT == 3600
    assert os_manager.OTA_MIN_RATE == 16384
    assert os_manager.OTA_TOTAL_TIMEOUT_MAX == 86400
    assert os_manager.OTA_MAX_ATTEMPTS == 8
    assert os_manager.ota_total_budget(None) == 3600
    assert os_manager.ota_total_budget(210 * 1024 * 1024) == 13440
    assert os_manager.ota_total_budget(10 * 1024**3) == 86400


async def test_clean_download_is_verified(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """A clean download is verified and renamed into place."""
    data = bytes(range(256)) * (SIZE // 256)
    store.put(data)
    target = tmp_path / "hassos-18.4.1.7.raucb"
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert target.read_bytes() == data
    assert not list(tmp_path.glob("*.part*"))


async def test_cuts_are_resumed_with_range_and_if_range(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """Three dropped connections: resumed, not restarted (~1.0x bytes)."""
    data = bytes(range(256)) * (SIZE // 256)
    store.put(data)
    store.cut_after = 1024 * 1024
    store.cut_count = 3
    target = tmp_path / "hassos-18.4.1.7.raucb"
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert target.read_bytes() == data
    reqs = store.bundle_requests()
    resumed = [r for r in reqs[1:] if r["range"] and r["if_range"] == store.etag]
    assert len(resumed) == 3, reqs
    assert all(r["status"] == 206 for r in resumed)
    # per cut, aiohttp's read-ahead buffer is lost and fetched again (~200 KB measured)
    assert sum(r["sent"] for r in reqs) <= len(data) + 3 * 256 * 1024


async def test_sha_mismatch_is_never_handed_to_rauc(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """A bundle that does not match its sidecar never reaches RAUC."""
    data = b"x" * SIZE
    store.put(data, sidecar=f"{'0' * 64}  {NAME}\n")
    target = tmp_path / "hassos-18.4.1.7.raucb"
    with pytest.raises(HassOSUpdateError, match="sha256 mismatch"):
        await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert not target.exists()
    assert not (tmp_path / f"{target.name}.part").exists()


async def test_no_sidecar_is_announced_and_relies_on_rauc(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff, caplog
):
    """Without a sidecar the download succeeds, loudly."""
    data = b"y" * SIZE
    store.put(data, sidecar=None)
    target = tmp_path / "hassos-18.4.1.7.raucb"
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert target.read_bytes() == data
    assert "relying on the RAUC signature check only" in caplog.text


async def test_404_bundle_is_permanent(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """A missing bundle is not retried."""
    store.put(b"z" * 10)
    store.missing.add(NAME)
    target = tmp_path / "hassos-18.4.1.7.raucb"
    with pytest.raises(HassOSUpdateError, match="404"):
        await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert len(store.bundle_requests()) == 1


async def test_server_error_is_retried(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """A 5xx is retried on the same URL."""
    data = b"w" * SIZE
    store.put(data)
    store.fail_status = [503, 502]
    target = tmp_path / "hassos-18.4.1.7.raucb"
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert target.read_bytes() == data
    assert [r["status"] for r in store.bundle_requests()][:3] == [503, 502, 200]


async def test_partial_survives_a_failed_call_and_is_resumed(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """The old code deleted the partial; a new update call must continue it."""
    data = bytes(range(256)) * (SIZE // 256)
    store.put(data)
    store.cut_after = 1024 * 1024
    store.cut_count = 1
    target = tmp_path / "hassos-18.4.1.7.raucb"
    with (
        patch.object(os_manager, "OTA_MAX_ATTEMPTS", 1),
        pytest.raises(HassOSUpdateError),
    ):
        await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    part = tmp_path / f"{target.name}.part"
    assert part.exists()
    assert part.stat().st_size >= 1024 * 1024 - 256 * 1024
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert target.read_bytes() == data
    last = store.bundle_requests()[-1]
    assert last["status"] == 206
    assert last["range"].startswith("bytes=")


async def test_store_without_ranges_overwrites_the_partial(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """A store that ignores Range gets the partial overwritten, not appended."""
    data = bytes(range(256)) * (SIZE // 256)
    store.put(data)
    store.no_ranges = True
    store.cut_after = 1024 * 1024
    store.cut_count = 1
    target = tmp_path / "hassos-18.4.1.7.raucb"
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert target.read_bytes() == data


async def test_partials_of_other_versions_are_removed(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """Partials of other versions do not accumulate in tmp."""
    stale = tmp_path / "hassos-18.4.1.6.raucb.part"
    stale.write_bytes(b"old")
    store.put(b"v" * SIZE)
    target = tmp_path / "hassos-18.4.1.7.raucb"
    await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
    assert not stale.exists()


async def test_total_budget_ends_a_trickling_download(
    coresys: CoreSys, store: Store, tmp_path: Path, no_backoff
):
    """Size-aware but finite: slower than OTA_MIN_RATE fails."""
    store.put(b"t" * SIZE)
    target = tmp_path / "hassos-18.4.1.7.raucb"
    with (
        patch.object(os_manager, "OTA_TOTAL_TIMEOUT", 0),
        patch.object(os_manager, "OTA_MIN_RATE", 10**12),
        pytest.raises(HassOSUpdateError, match="exceeded the total timeout"),
    ):
        await coresys.os._download_raucb(store.url, target)  # noqa: SLF001
