"""GA: plugin updates follow the auto_update setting, like add-ons, Core and Supervisor.

With ``auto_update`` off, the Supervisor must not move a plugin (cli, dns, audio,
multicast, observer) to a newer version on its own — not from the periodic
tasks, not at startup, and not as a side effect of a Core or add-on update. An
operator moves plugins through the API (``POST /<plugin>/update``). Installing a
plugin whose image is missing stays automatic: without it the system does not run.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from awesomeversion import AwesomeVersion
import pytest

from supervisor.const import CoreState
from supervisor.coresys import CoreSys
from supervisor.docker.interface import DockerInterface
from supervisor.host.manager import HostManager
from supervisor.jobs.const import JobCondition
from supervisor.jobs.decorator import Job
from supervisor.misc.tasks import Tasks
from supervisor.plugins.audio import PluginAudio
from supervisor.plugins.base import PluginBase
from supervisor.plugins.cli import PluginCli
from supervisor.plugins.dns import PluginDns
from supervisor.plugins.multicast import PluginMulticast
from supervisor.plugins.observer import PluginObserver
from supervisor.supervisor import Supervisor

from tests.common import MockResponse

# pylint: disable=protected-access

PLUGIN_TASKS = [
    ("_update_cli", PluginCli),
    ("_update_dns", PluginDns),
    ("_update_audio", PluginAudio),
    ("_update_observer", PluginObserver),
    ("_update_multicast", PluginMulticast),
]


@pytest.fixture(name="tasks")
async def fixture_tasks(
    coresys: CoreSys, container: MagicMock
) -> AsyncGenerator[Tasks]:
    """Return a task manager whose plugin-update conditions are otherwise met."""
    coresys.hardware.disk.get_disk_free_space = lambda x: 5000
    container.status = "running"
    await coresys.core.set_state(CoreState.RUNNING)
    yield Tasks(coresys)


@pytest.mark.parametrize(("task", "plugin_cls"), PLUGIN_TASKS)
@pytest.mark.usefixtures("no_job_throttle", "supervisor_internet")
async def test_periodic_plugin_update_blocked_when_auto_update_off(
    coresys: CoreSys, tasks: Tasks, task: str, plugin_cls: type[PluginBase]
):
    """The scheduled plugin update does nothing while auto_update is off."""
    coresys.updater.auto_update = False
    with (
        patch.object(plugin_cls, "need_update", new=PropertyMock(return_value=True)),
        patch.object(plugin_cls, "update", new_callable=AsyncMock) as update,
    ):
        await getattr(tasks, task)()
    update.assert_not_called()


@pytest.mark.parametrize(("task", "plugin_cls"), PLUGIN_TASKS)
@pytest.mark.usefixtures("no_job_throttle", "supervisor_internet")
async def test_periodic_plugin_update_runs_when_auto_update_on(
    coresys: CoreSys, tasks: Tasks, task: str, plugin_cls: type[PluginBase]
):
    """Upstream behaviour is unchanged when auto_update is on."""
    coresys.updater.auto_update = True
    with (
        patch.object(plugin_cls, "need_update", new=PropertyMock(return_value=True)),
        patch.object(plugin_cls, "update", new_callable=AsyncMock) as update,
    ):
        await getattr(tasks, task)()
    update.assert_called_once()


@pytest.mark.parametrize("auto_update", [False, True])
@pytest.mark.usefixtures("no_job_throttle")
async def test_startup_plugin_update_follows_auto_update(
    coresys: CoreSys,
    mock_update_data: MockResponse,
    supervisor_internet: AsyncMock,
    auto_update: bool,
):
    """Startup (``PluginManager.load``) updates outdated plugins only with auto_update on."""
    coresys.hardware.disk.get_disk_free_space = lambda x: 5000
    await coresys.updater.load()
    await coresys.updater.reload()
    coresys.updater.auto_update = auto_update

    with (
        patch.object(DockerInterface, "attach") as attach,
        patch.object(DockerInterface, "update") as update,
        patch.object(Supervisor, "need_update", new=PropertyMock(return_value=False)),
        patch.object(PluginBase, "need_update", new=PropertyMock(return_value=True)),
        patch.object(
            PluginBase,
            "version",
            new=PropertyMock(return_value=AwesomeVersion("1970-01-01")),
        ),
    ):
        await coresys.plugins.load()

    # Every plugin is still loaded (attached) either way.
    assert attach.call_count == 5
    assert update.call_count == (5 if auto_update else 0)


async def test_missing_plugin_image_still_installed_when_auto_update_off(
    coresys: CoreSys,
):
    """Repair installs a plugin whose image is gone, regardless of auto_update."""
    coresys.updater.auto_update = False

    async def _exists(*args, **kwargs) -> bool:
        return False

    with (
        patch.object(DockerInterface, "exists", new=_exists),
        patch.object(DockerInterface, "install") as install,
    ):
        await coresys.plugins.repair()

    assert install.call_count == len(coresys.plugins.all_plugins)


class _NeedsPlugins:
    """A job that, like a Core or add-on update, requires current plugins."""

    def __init__(self, coresys: CoreSys):
        self.coresys = coresys

    @Job(name="test_ga_plugins_updated", conditions=[JobCondition.PLUGINS_UPDATED])
    async def execute(self) -> bool:
        return True


async def test_core_or_addon_update_does_not_pull_plugins_when_auto_update_off(
    coresys: CoreSys, caplog: pytest.LogCaptureFixture
):
    """PLUGINS_UPDATED does not update plugins on the side while auto_update is off.

    The job runs, the outdated plugin stays where it is, and the log names it.
    """
    coresys.updater.auto_update = False
    test = _NeedsPlugins(coresys)
    with (
        patch.object(PluginAudio, "need_update", new=PropertyMock(return_value=True)),
        patch.object(PluginAudio, "update", new=AsyncMock(return_value=None)) as update,
    ):
        assert await test.execute()
    update.assert_not_called()
    assert "audio" in caplog.text
    assert "auto update" in caplog.text.lower()


async def test_core_or_addon_update_pulls_plugins_when_auto_update_on(
    coresys: CoreSys,
):
    """Upstream behaviour is unchanged when auto_update is on."""
    coresys.updater.auto_update = True
    test = _NeedsPlugins(coresys)
    with (
        patch.object(PluginAudio, "need_update", new=PropertyMock(return_value=True)),
        patch.object(PluginAudio, "update", new=AsyncMock(return_value=None)) as update,
    ):
        assert await test.execute()
    update.assert_called_once()


class _NeedsPluginsAndMount:
    """A job with a condition checked after PLUGINS_UPDATED."""

    def __init__(self, coresys: CoreSys):
        self.coresys = coresys

    @Job(
        name="test_ga_plugins_updated_then_mount",
        conditions=[JobCondition.PLUGINS_UPDATED, JobCondition.MOUNT_AVAILABLE],
    )
    async def execute(self) -> bool:
        return True


async def test_skipped_plugin_update_still_checks_later_conditions(coresys: CoreSys):
    """Skipping the plugin update must not skip the conditions checked after it."""
    coresys.updater.auto_update = False
    test = _NeedsPluginsAndMount(coresys)
    with (
        # no MOUNT host feature -> MOUNT_AVAILABLE fails
        patch.object(HostManager, "features", new=PropertyMock(return_value=[])),
        patch.object(PluginAudio, "need_update", new=PropertyMock(return_value=True)),
        patch.object(PluginAudio, "update", new=AsyncMock(return_value=None)),
    ):
        assert not await test.execute()
