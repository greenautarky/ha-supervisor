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

        GreenAutarky delta (v1.2): the entire GA fleet is the Sonoff iHost
        (RV1126 SoC) which is permanently armv7 (32-bit ARM) — it is the
        hardware, not a transitional setup. GA also serves its own version
        JSON (URL_HASSIO_VERSION -> greenautarky/haos-version), so the
        supervisor does not depend on upstream dropping 32-bit support.

        Upstream now flags armv7 as "no longer supported", which adds
        UnsupportedReason.SYSTEM_ARCHITECTURE to the resolution center.
        That in turn trips JobCondition.ARCHITECTURE_SUPPORTED and blocks
        Updater.fetch_data (and every other arch-gated job) — on a fresh
        device the supervisor then never gets HA version info and never
        installs Core. We therefore keep armv7 OUT of the unsupported set
        so all arch-gated jobs keep working on the GA fleet.

        (i386/armhf remain flagged — GA does not ship those architectures.)
        """
        return self.sys_host.info.sys_arch.supervisor in {
            "i386",
            "armhf",
        }
