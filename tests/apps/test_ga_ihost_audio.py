"""GA fork: audio apps installed on an iHost default to its speaker sink."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from supervisor.apps.data import AppsData
from supervisor.coresys import CoreSys
from supervisor.os.manager import OSManager


@pytest.mark.parametrize(
    ("board", "with_audio", "expected"),
    [
        ("ihost", True, "alsa_output.1.stereo-fallback"),
        ("ihost", False, None),
        ("generic-aarch64", True, None),
        (None, True, None),
    ],
)
async def test_install_sets_ihost_fallback_sink(
    coresys: CoreSys, board: str | None, with_audio: bool, expected: str | None
):
    """Only an audio app on board 'ihost' gets the fallback sink."""
    app = MagicMock(slug="test_app", data={}, version="1.0", image="test/image")
    app.with_audio = with_audio
    data = coresys.apps.data

    with (
        patch.object(OSManager, "board", new=PropertyMock(return_value=board)),
        patch.object(AppsData, "save_data", new=AsyncMock()),
    ):
        await data.install(app)

    assert data.user["test_app"]["audio_output"] == expected
