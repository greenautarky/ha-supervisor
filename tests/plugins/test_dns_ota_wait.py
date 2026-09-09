"""The OTA-endpoint wait in PluginDns, which nothing asserted until now.

`_load_ga_ota_ip` polls `/run/ga-resolve-ota.active` for up to
`_GA_OTA_ACTIVE_WAIT_S` seconds. On a device that closes a real boot race: if
the Supervisor's CoreDNS resolves ota.greenautarky.com before
ga-resolve-ota.service has picked an address, host and Supervisor disagree and
an OTA fails (observed on K7, BOSv1.2.12, 2026-06-10).

The rest of the suite patches that budget to zero, because the file cannot
appear in a test container and every call would otherwise burn fifteen seconds.
These tests are where the waiting is actually exercised, so that the patch
elsewhere hides nothing.
"""

from unittest.mock import patch

import pytest

from supervisor.plugins.dns import PluginDns


class _FakeClock:
    """Stands in for the `time` MODULE inside supervisor.plugins.dns.

    Deliberately not `patch("supervisor.plugins.dns.time.sleep")`: that form
    resolves supervisor.plugins.dns.time to the real stdlib module and patches
    it PROCESS-WIDE, so asyncio's own clock gets the mock too. A side_effect
    iterator then raises StopIteration in whatever unrelated code calls
    time.monotonic() next — including inside garbage collection, where it
    surfaces as an unraisable exception attributed to some other test entirely.
    Patching the NAME in the module's namespace keeps it local.

    Time advances only when the code under test sleeps, so the wait is driven
    rather than waited out.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _FakePath:
    """A path that answers is_file()/read_text() from a script, not a disk."""

    def __init__(self, present: bool, content: str = "") -> None:
        self._present = present
        self._content = content
        self.is_file_calls = 0

    def is_file(self) -> bool:
        self.is_file_calls += 1
        return self._present

    def read_text(self, encoding: str = "utf-8") -> str:  # noqa: ARG002
        return self._content


@pytest.fixture(name="ga_ota_active_no_wait", autouse=False)
def fixture_opt_out():
    """Opt out of the suite-wide zero-wait patch — the wait is the subject here."""
    yield


def test_the_active_file_is_used_and_nothing_waits():
    """The happy path must not sleep at all."""
    fake = _FakePath(present=True, content="100.126.0.9\n")

    clock = _FakeClock()

    with (
        patch("supervisor.plugins.dns.Path", return_value=fake),
        patch("supervisor.plugins.dns.time", clock),
    ):
        assert PluginDns._load_ga_ota_ip() == "100.126.0.9"

    assert clock.sleeps == []


def test_the_wait_is_bounded_and_then_falls_back():
    """When the file never appears the wait ends and the fallback chain runs.

    Asserted through the mocked clock rather than by sleeping: a test that
    really waits is a test people delete.
    """
    fake = _FakePath(present=False)
    clock = _FakeClock()

    with (
        patch("supervisor.plugins.dns.Path", return_value=fake),
        patch("supervisor.plugins.dns.time", clock),
        patch.object(PluginDns, "_read_ga_conf_value", return_value=None),
    ):
        result = PluginDns._load_ga_ota_ip()

    # It gave up rather than looping forever, and it spent the whole budget
    # doing so — asserting merely that it slept once would pass on a loop that
    # gives up immediately.
    assert sum(clock.sleeps) >= PluginDns._GA_OTA_ACTIVE_WAIT_S - max(clock.sleeps)
    # With no conf file to read, the last resort is the pinned constant.
    assert result == PluginDns._GA_HARDCODED_FALLBACK_IP


def test_the_budget_is_a_real_number_of_seconds():
    """Guard the constant itself.

    A zero here would silently remove the boot-race protection while every
    other test in the suite stayed green, because they all patch it to zero.
    """
    assert PluginDns._GA_OTA_ACTIVE_WAIT_S >= 1
