"""GA fork: the Supervisor never reads the upstream version feed.

This file deliberately imports nothing from the GA patch set (no
supervisor.ga_version_url), so it runs unchanged on plain upstream and FAILS
there on its assertions - that is its red proof. On the GA fork it passes.

What it pins, all against literals (never against supervisor.const, which is
the thing under test):

- with no usable /etc/ga-version-url, fetch_data requests the GA feed, not
  version.home-assistant.io;
- that substitution is reported at ERROR level, not silently or at INFO.
"""

import logging
import sys
from unittest.mock import AsyncMock, patch

import pytest

from supervisor.coresys import CoreSys

from tests.common import MockResponse

GA_FEED_PREFIX = "https://raw.githubusercontent.com/greenautarky/haos-version/"
GA_MAIN_STABLE = GA_FEED_PREFIX + "main/stable.json"
UPSTREAM_FEED_HOST = "version.home-assistant.io"


@pytest.mark.usefixtures("no_job_throttle")
async def test_no_version_url_file_reads_ga_feed_and_logs_error(
    coresys: CoreSys,
    tmp_path,
    mock_update_data: MockResponse,
    supervisor_internet: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing host file: GA main feed, an ERROR line, never the upstream feed."""
    coresys.security.force = True
    missing = tmp_path / "no-ga-version-url"

    # Hermetic on the fork; a no-op on plain upstream (module absent there).
    ga_module = sys.modules.get("supervisor.ga_version_url")
    ctx = (
        patch.object(ga_module, "FILE_GA_VERSION_URL", missing)
        if ga_module is not None
        else patch.dict({})
    )
    with ctx, caplog.at_level(logging.INFO):
        await coresys.updater.load_config()
        await coresys.updater.fetch_data()

    requested = coresys.websession.get.call_args[0][0]
    assert UPSTREAM_FEED_HOST not in requested
    assert requested == GA_MAIN_STABLE

    fallback_errors = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "falling back" in r.getMessage()
    ]
    assert len(fallback_errors) == 1, "the fallback must be reported at ERROR"
