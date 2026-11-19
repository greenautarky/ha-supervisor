"""GA fork: an image pull can neither stall nor trickle forever.

The constants are patched to milliseconds here; their production values are
pinned separately so that shrinking them to zero is caught.
"""

import asyncio
from unittest.mock import patch

import pytest

from supervisor.coresys import CoreSys
from supervisor.docker import manager as docker_manager
from supervisor.exceptions import DockerError


def _pull_stream(events: int, delay: float):
    """Return a fake images.pull that yields `events` entries `delay` apart."""

    async def _gen(*args, **kwargs):
        for _ in range(events):
            await asyncio.sleep(delay)
            yield {"status": "Downloading", "id": "layer"}

    return _gen


def test_pull_timeouts_are_real_numbers():
    """Production values: 300 s stall, 30 min minimum total (pinned literals).

    300 s, not 90: a baked add-on image is unpacked by the daemon during the
    pull without emitting progress events (~25 s + 0.25 s/MB compressed on the
    iHost; ga_hmvapp aborted at 90 s on BOSv1.6.0-rc13, Odoo #2065). The total
    cap stays the hard upper bound, and the stall window must stay below it.
    """
    assert docker_manager.PULL_STALL_TIMEOUT == 300
    assert docker_manager.PULL_TOTAL_TIMEOUT == 1800
    assert docker_manager.PULL_STALL_TIMEOUT < docker_manager.PULL_TOTAL_TIMEOUT
    # Odoo #2136: size-aware budget. 16 KiB/s is below the slowest field link
    # measured (60 KB/s); 24 h bounds any pull.
    assert docker_manager.PULL_MIN_RATE == 16384
    assert docker_manager.PULL_TOTAL_TIMEOUT_MAX == 86400


async def test_stalled_pull_raises(coresys: CoreSys):
    """No progress event within the stall window: DockerError, not a hang."""
    coresys.docker.images.pull = _pull_stream(events=2, delay=1.0)

    with (
        patch.object(docker_manager, "PULL_STALL_TIMEOUT", 0.05),
        pytest.raises(DockerError, match="stalled"),
    ):
        async with asyncio.timeout(5):
            await coresys.docker.pull_image("job", "test/image", "1.0")


async def test_progressing_pull_is_not_cut_by_the_stall_timeout(coresys: CoreSys):
    """Each progress event moves the stall deadline: a slow pull completes."""
    coresys.docker.images.pull = _pull_stream(events=5, delay=0.03)

    with patch.object(docker_manager, "PULL_STALL_TIMEOUT", 0.1):
        await coresys.docker.pull_image("job", "test/image", "1.0")

    coresys.docker.images.inspect.assert_called_with("test/image:1.0")


async def test_trickling_pull_hits_the_total_timeout(coresys: CoreSys):
    """A pull that keeps progressing but never ends is capped."""
    coresys.docker.images.pull = _pull_stream(events=1000, delay=0.01)

    with (
        patch.object(docker_manager, "PULL_STALL_TIMEOUT", 1.0),
        patch.object(docker_manager, "PULL_TOTAL_TIMEOUT", 0.1),
        pytest.raises(DockerError, match="total timeout"),
    ):
        async with asyncio.timeout(5):
            await coresys.docker.pull_image("job", "test/image", "1.0")


def _sized_pull_stream(events: int, delay: float, layer_total: int):
    """Like _pull_stream, but every event announces a layer of layer_total bytes."""

    async def _gen(*args, **kwargs):
        for i in range(events):
            await asyncio.sleep(delay)
            yield {
                "status": "Downloading",
                "id": "layer",
                "progressDetail": {"current": i, "total": layer_total},
            }

    return _gen


@pytest.mark.parametrize(
    ("known", "budget"),
    [
        (0, 1800),  # nothing announced yet: the minimum
        (10 * 1024 * 1024, 1800),  # small image: 640 s by size, minimum wins
        (800 * 1024 * 1024, 51200),  # Core-sized: 800 MiB / 16 KiB/s
        (10 * 1024**3, 86400),  # absurd size: hard cap
    ],
)
def test_pull_total_budget(known: int, budget: float):
    """The budget grows with the size, between the minimum and the hard cap."""
    assert docker_manager.pull_total_budget(known) == budget


def test_layer_sizes_come_only_from_downloading_events():
    """Extracting reports elapsed time in progressDetail; it must not count."""
    sizes: dict[str, int] = {}
    for event in (
        {"status": "Downloading", "id": "a", "progressDetail": {"total": 100}},
        {"status": "Downloading", "id": "a", "progressDetail": {"total": 100}},
        {"status": "Downloading", "id": "b", "progressDetail": {"total": 50}},
        {"status": "Extracting", "id": "c", "progressDetail": {"total": 9999}},
        {"status": "Downloading", "id": "d", "progressDetail": {"total": True}},
        {"status": "Downloading", "id": "e", "progressDetail": "broken"},
        {"status": "Downloading", "progressDetail": {"total": 7}},
    ):
        docker_manager.record_layer_size(sizes, event)
    assert sizes == {"a": 100, "b": 50}


async def test_large_slow_pull_outlives_the_fixed_minimum(coresys: CoreSys):
    """Odoo #2136: a big image on a slow link is NOT cut at PULL_TOTAL_TIMEOUT.

    Scaled: minimum 0.1 s, the pull takes ~0.3 s, and the announced size at
    PULL_MIN_RATE allows 10 s. Before the size-aware budget this raised.
    """
    coresys.docker.images.pull = _sized_pull_stream(
        events=15, delay=0.02, layer_total=1000
    )

    with (
        patch.object(docker_manager, "PULL_STALL_TIMEOUT", 1.0),
        patch.object(docker_manager, "PULL_TOTAL_TIMEOUT", 0.1),
        patch.object(docker_manager, "PULL_MIN_RATE", 100),
    ):
        async with asyncio.timeout(5):
            await coresys.docker.pull_image("job", "test/image", "1.0")

    coresys.docker.images.inspect.assert_called_with("test/image:1.0")


async def test_sized_pull_slower_than_the_floor_is_still_capped(coresys: CoreSys):
    """The budget grows with the size but still ends: slower than PULL_MIN_RATE fails."""
    coresys.docker.images.pull = _sized_pull_stream(
        events=1000, delay=0.01, layer_total=10
    )

    with (
        patch.object(docker_manager, "PULL_STALL_TIMEOUT", 1.0),
        patch.object(docker_manager, "PULL_TOTAL_TIMEOUT", 0.05),
        patch.object(docker_manager, "PULL_MIN_RATE", 50),
        pytest.raises(DockerError, match="total timeout"),
    ):
        async with asyncio.timeout(5):
            await coresys.docker.pull_image("job", "test/image", "1.0")
