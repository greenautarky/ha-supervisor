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

    with (
        patch("supervisor.plugins.dns.Path", return_value=fake),
        patch("supervisor.plugins.dns.time.sleep") as sleep,
    ):
        assert PluginDns._load_ga_ota_ip() == "100.126.0.9"

    sleep.assert_not_called()


def test_the_wait_is_bounded_and_then_falls_back():
    """When the file never appears the wait ends and the fallback chain runs.

    Asserted through the mocked clock rather than by sleeping: a test that
    really waits is a test people delete.
    """
    fake = _FakePath(present=False)
    clock = iter([0.0, 0.0, 5.0, 10.0, 15.0, 20.0, 25.0])

    with (
        patch("supervisor.plugins.dns.Path", return_value=fake),
        patch("supervisor.plugins.dns.time.sleep") as sleep,
        patch("supervisor.plugins.dns.time.monotonic", side_effect=lambda: next(clock)),
        patch.object(PluginDns, "_read_ga_conf_value", return_value=None),
    ):
        result = PluginDns._load_ga_ota_ip()

    # It gave up rather than looping forever, and it slept while trying.
    assert sleep.called
    # With no conf file to read, the last resort is the pinned constant.
    assert result == PluginDns._GA_HARDCODED_FALLBACK_IP


def test_the_budget_is_a_real_number_of_seconds():
    """Guard the constant itself.

    A zero here would silently remove the boot-race protection while every
    other test in the suite stayed green, because they all patch it to zero.
    """
    assert PluginDns._GA_OTA_ACTIVE_WAIT_S >= 1
