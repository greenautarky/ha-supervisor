"""armv7 must stay supported, and the guard around it must be load-bearing.

Upstream 93272fe4c (#5620) made `EvaluateSystemArchitecture` return unsupported
for armv7, and 2025.11.5 attaches `JobCondition.ARCHITECTURE_SUPPORTED` to
`Updater.fetch_data`, to `PLUGIN_UPDATE_CONDITIONS` and to the supervisor
update task. Our fleet is permanently armv7 (Sonoff iHost, RV1126), so taken
raw that combination stops a device reading `stable.json` and stops every
plugin update — fleet-wide and permanently. It has happened once already, on a
fresh V1.2-clean device (cef383b7c, 2026-05-22).

Three things have to hold together, and any one of them alone proves nothing:

1. armv7 is not in the unsupported set;
2. the condition really does block when something IS in it — otherwise (1) is
   a statement about a guard that does not guard;
3. the condition is still attached to the jobs it exists to protect —
   otherwise (1) and (2) are both true of a mechanism nothing uses.

(3) is asserted by calling the real job and watching it refuse, not by reading
the decorator's condition list: a list read cannot tell the difference between
"the condition is attached" and "the condition fell off in a rebase and I am
reading the same place it fell off from".
"""

from unittest.mock import PropertyMock, patch

import pytest

from supervisor.const import CoreState
from supervisor.coresys import CoreSys
from supervisor.exceptions import UpdaterJobError
from supervisor.jobs.const import JobCondition
from supervisor.jobs.decorator import Job
from supervisor.plugins.const import PLUGIN_UPDATE_CONDITIONS
from supervisor.resolution.const import UnsupportedReason
from supervisor.resolution.evaluations.system_architecture import (
    EvaluateSystemArchitecture,
)


class _ArchGatedJob:
    """Smallest possible consumer of the condition."""

    def __init__(self, coresys: CoreSys):
        self.coresys = coresys

    @Job(
        name="test_arch_gated_job",
        conditions=[JobCondition.ARCHITECTURE_SUPPORTED],
    )
    async def execute(self) -> bool:
        """Return True only if the condition let us through."""
        return True


async def test_armv7_leaves_arch_gated_jobs_runnable(coresys: CoreSys):
    """(1)+(2) green half: the real evaluation on armv7 does not block a job."""
    evaluation = EvaluateSystemArchitecture(coresys)
    await coresys.core.set_state(CoreState.INITIALIZE)

    with patch.object(
        type(coresys.supervisor), "arch", PropertyMock(return_value="armv7")
    ):
        await evaluation()

    assert UnsupportedReason.SYSTEM_ARCHITECTURE not in coresys.resolution.unsupported
    assert await _ArchGatedJob(coresys).execute()


async def test_arch_gated_job_blocks_when_the_architecture_is_flagged(
    coresys: CoreSys,
):
    """(2) red half: the guard is not decorative — flag it and the job stops.

    This is the failure the armv7 delta exists to prevent, injected on purpose
    so that the guard is proven able to fire (a check that has never failed is
    not evidence).
    """
    coresys.resolution.add_unsupported_reason(UnsupportedReason.SYSTEM_ARCHITECTURE)

    assert not await _ArchGatedJob(coresys).execute()


async def test_updater_fetch_data_carries_the_architecture_condition(
    coresys: CoreSys,
    supervisor_internet,
):
    """(3) `Updater.fetch_data` must actually be gated by it.

    Asserted through the job, not through the decorator's list: fetch_data has
    to refuse before it reads anything. This is the exact path that leaves a
    device unable to see stable.json again.

    `supervisor_internet` is required because INTERNET_SYSTEM is checked
    before ARCHITECTURE_SUPPORTED; without it the job dies on a missing
    websession and never reaches the condition under test.
    """
    coresys.resolution.add_unsupported_reason(UnsupportedReason.SYSTEM_ARCHITECTURE)

    with pytest.raises(UpdaterJobError):
        await coresys.updater.fetch_data()


def test_plugin_updates_are_gated_by_the_architecture_condition():
    """(3) for plugins.

    Read from the definition rather than driven through a plugin update: the
    behavioural equivalent would need a full plugin install, and the list is
    the definition itself rather than a copy of one.
    """
    assert JobCondition.ARCHITECTURE_SUPPORTED in PLUGIN_UPDATE_CONDITIONS
