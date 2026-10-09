"""Evaluation class for system architecture support."""

from ...const import CoreState
from ...coresys import CoreSys
from ..const import UnsupportedReason
from .base import EvaluateBase


def setup(coresys: CoreSys) -> EvaluateBase:
    """Initialize evaluation-setup function."""
    return EvaluateSystemArchitecture(coresys)


class EvaluateSystemArchitecture(EvaluateBase):
    """Evaluate if the current Supervisor architecture is supported."""

    @property
    def reason(self) -> UnsupportedReason:
        """Return a UnsupportedReason enum."""
        return UnsupportedReason.SYSTEM_ARCHITECTURE

    @property
    def on_failure(self) -> str:
        """Return a string that is printed when self.evaluate is True."""
        return "System architecture is no longer supported. Move to a supported system architecture."

    @property
    def states(self) -> list[CoreState]:
        """Return a list of valid states when this evaluation can run."""
        return [CoreState.INITIALIZE]

    async def evaluate(self):
        """Run evaluation.

        GA fork: armv7 is not in the unsupported set. The GA iHost fleet is
        permanently armv7 and GA serves its own version feed. Upstream flags
        armv7, which adds UnsupportedReason.SYSTEM_ARCHITECTURE, which fails
        JobCondition.ARCHITECTURE_SUPPORTED, which blocks Updater.fetch_data,
        plugin updates and the Supervisor update: a fresh device would never
        read its channel and never install Core.
        """
        return self.sys_supervisor.arch in {
            "i386",
            "armhf",
        }
