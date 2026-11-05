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
    """Production values: 90 s stall, 30 min total (pinned literals)."""
    assert docker_manager.PULL_STALL_TIMEOUT == 90
    assert docker_manager.PULL_TOTAL_TIMEOUT == 1800


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
