"""GA fork (F1): a channel Core image is adopted only if it fits the device.

Measured on DUT2 (DSGW, qemuarm-64) 2026-10-09: the GA channel named
ghcr.io/greenautarky/home-assistant-armv7 and the Supervisor announced it would
take over at the next Core update - an armv7 Core on an aarch64 device.
"""

# pylint: disable=protected-access

import logging
from unittest.mock import MagicMock, PropertyMock, patch

from awesomeversion import AwesomeVersion
import pytest

from supervisor.const import ATTR_HOMEASSISTANT, ATTR_IMAGE, CpuArch
from supervisor.coresys import CoreSys
from supervisor.homeassistant.module import _image_arch_markers

GA_ARMV7 = "ghcr.io/greenautarky/home-assistant-armv7"


def _device(coresys: CoreSys, sys_machine: PropertyMock, machine: str, arch: CpuArch):
    sys_machine.return_value = machine
    coresys.arch._default_arch = arch
    coresys.arch._supported_arch = [arch]
    coresys.arch._supported_set = {arch}


def _channel(coresys: CoreSys, image: str):
    coresys.updater._data[ATTR_IMAGE][ATTR_HOMEASSISTANT] = image


async def test_armv7_device_adopts_armv7_channel_image(
    coresys: CoreSys, sys_machine: PropertyMock, caplog: pytest.LogCaptureFixture
):
    """armv7 iHost: the GA armv7 build is adopted (3451f6987 unchanged)."""
    _device(coresys, sys_machine, "ihost", CpuArch.ARMV7)
    _channel(coresys, GA_ARMV7)

    assert coresys.homeassistant.default_image == GA_ARMV7
    assert coresys.homeassistant.install_image == GA_ARMV7
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_aarch64_device_refuses_armv7_channel_image(
    coresys: CoreSys, sys_machine: PropertyMock, caplog: pytest.LogCaptureFixture
):
    """aarch64 DSGW: an armv7-only channel image is refused loudly, never adopted."""
    _device(coresys, sys_machine, "qemuarm-64", CpuArch.AARCH64)
    _channel(coresys, GA_ARMV7)
    upstream = "ghcr.io/home-assistant/qemuarm-64-homeassistant"

    assert coresys.homeassistant.default_image == upstream
    assert coresys.homeassistant.install_image == upstream
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert GA_ARMV7 in errors[0].getMessage()
    assert upstream in errors[0].getMessage()
    # Logged once per refused image, not on every property read.
    _ = coresys.homeassistant.default_image
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/x/home-assistant-armhf",
        "ghcr.io/x/i386-homeassistant",
        "ghcr.io/x/amd64-homeassistant",
    ],
)
async def test_aarch64_device_refuses_foreign_arch_markers(
    coresys: CoreSys, sys_machine: PropertyMock, image: str
):
    """aarch64: armhf/i386/amd64 markers are refused too."""
    _device(coresys, sys_machine, "qemuarm-64", CpuArch.AARCH64)
    _channel(coresys, image)

    assert coresys.homeassistant.channel_image is None


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        (
            "ghcr.io/home-assistant/{machine}-homeassistant",
            "ghcr.io/home-assistant/qemuarm-64-homeassistant",
        ),
        (
            "ghcr.io/greenautarky/home-assistant-{arch}",
            "ghcr.io/greenautarky/home-assistant-aarch64",
        ),
    ],
)
async def test_aarch64_device_adopts_resolved_template(
    coresys: CoreSys, sys_machine: PropertyMock, template: str, expected: str
):
    """aarch64: a {machine}/{arch} template is adopted, resolved for the device."""
    _device(coresys, sys_machine, "qemuarm-64", CpuArch.AARCH64)
    _channel(coresys, template)

    assert coresys.homeassistant.default_image == expected
    assert coresys.homeassistant.install_image == expected


async def test_aarch64_device_keeps_explicit_user_override(
    coresys: CoreSys,
    sys_machine: PropertyMock,
    container: MagicMock,
    caplog: pytest.LogCaptureFixture,
):
    """aarch64: an explicit override is kept and the armv7 channel image is not pulled."""
    _device(coresys, sys_machine, "qemuarm-64", CpuArch.AARCH64)
    _channel(coresys, GA_ARMV7)
    override = "ghcr.io/greenautarky/home-assistant-aarch64"
    coresys.homeassistant.set_image(override)
    coresys.homeassistant.version = AwesomeVersion("2026.8.2")
    coresys.homeassistant.override_image = True
    container.show.return_value["Config"] = {"Labels": {"io.hass.version": "2026.8.2"}}

    await coresys.homeassistant.core.load()

    assert coresys.homeassistant.image == override
    assert coresys.homeassistant.override_image is True
    assert coresys.homeassistant.install_image == override
    coresys.docker.images.pull.assert_not_called()
    for call in coresys.docker.images.inspect.call_args_list:
        assert GA_ARMV7 not in str(call)


@pytest.mark.parametrize(
    ("image", "markers"),
    [
        ("ghcr.io/greenautarky/home-assistant-armv7", {"armv7"}),
        ("ghcr.io/home-assistant/qemuarm-64-homeassistant", set()),
        ("ghcr.io/home-assistant/home-assistant:2026.10.1", set()),
        ("ghcr.io/x/home-assistant-x86_64", {"x86_64"}),
        ("ghcr.io/x/arm64-homeassistant", {"arm64"}),
    ],
)
def test_image_arch_markers(image: str, markers: set[str]):
    """Arch markers are read from the image name only."""
    assert _image_arch_markers(image) == markers


# F1b (K49, armv7 iHost, 2026-10-12): Core.setup() runs HomeAssistant.load()
# before CpuArchManager.load(), so default_image was read with no arch loaded
# and HomeAssistantCore.load() died with AttributeError('_default_arch').


def _arch_not_loaded(coresys: CoreSys, supervisor_arch: str | None):
    del coresys.arch._default_arch
    coresys._supervisor.instance._meta = (
        {"Config": {"Labels": {"io.hass.arch": supervisor_arch}}}
        if supervisor_arch
        else {"Config": {"Labels": {}}}
    )


async def test_arch_not_loaded_armv7_uses_supervisor_arch(
    coresys: CoreSys, sys_machine: PropertyMock
):
    """K49: arch manager unloaded -> Supervisor arch armv7 -> GA image adopted."""
    sys_machine.return_value = "ihost"
    _arch_not_loaded(coresys, "armv7")
    _channel(coresys, GA_ARMV7)

    assert coresys.homeassistant.default_image == GA_ARMV7


async def test_arch_not_loaded_aarch64_still_refuses(
    coresys: CoreSys, sys_machine: PropertyMock
):
    """DSGW: arch manager unloaded -> Supervisor arch aarch64 -> still refused."""
    sys_machine.return_value = "qemuarm-64"
    _arch_not_loaded(coresys, "aarch64")
    _channel(coresys, GA_ARMV7)

    assert coresys.homeassistant.channel_image is None
    assert (
        coresys.homeassistant.default_image
        == "ghcr.io/home-assistant/qemuarm-64-homeassistant"
    )


async def test_arch_fully_unknown_adopts_with_warning(
    coresys: CoreSys, sys_machine: PropertyMock, caplog: pytest.LogCaptureFixture
):
    """No arch anywhere: adopt (no basis to refuse), WARNING once, never raise."""
    sys_machine.return_value = "ihost"
    _arch_not_loaded(coresys, None)
    _channel(coresys, GA_ARMV7)

    assert coresys.homeassistant.default_image == GA_ARMV7
    _ = coresys.homeassistant.default_image
    warns = [r for r in caplog.records if "architecture unknown" in r.getMessage()]
    assert len(warns) == 1


async def test_core_load_with_arch_not_loaded_does_not_raise(
    coresys: CoreSys, sys_machine: PropertyMock
):
    """The K49 trace: HomeAssistantCore.load() before arch load must not raise."""
    sys_machine.return_value = "ihost"
    _arch_not_loaded(coresys, "armv7")
    _channel(coresys, GA_ARMV7)
    coresys.homeassistant._data[ATTR_IMAGE] = GA_ARMV7
    coresys.homeassistant.version = AwesomeVersion("2026.10.0")
    coresys.homeassistant.core.instance.attach = MagicMock()
    coresys.homeassistant.core.instance.check_image = MagicMock()

    async def _noop(*_a, **_k):
        return None

    coresys.homeassistant.core.instance.attach.side_effect = _noop
    coresys.homeassistant.core.instance.check_image.side_effect = _noop
    with patch.object(
        type(coresys.homeassistant.core.instance),
        "version",
        new=PropertyMock(return_value=AwesomeVersion("2026.10.0")),
    ):
        await coresys.homeassistant.core.load()
    # Running GA image is the default: nothing is reconciled away.
    coresys.homeassistant.core.instance.check_image.assert_called_once_with(
        AwesomeVersion("2026.10.0"), GA_ARMV7
    )
