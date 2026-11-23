"""OS support on supervisor."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from functools import partial
import hashlib
import logging
from pathlib import Path, PurePath
import re
from typing import Final

import aiohttp
from awesomeversion import AwesomeVersion, AwesomeVersionException
from cpe import CPE

from ..coresys import CoreSys
from ..dbus.agent.boards.const import BOARD_NAME_SUPERVISED
from ..dbus.rauc import RaucState, SlotStatusDataType
from ..exceptions import (
    DBusError,
    DBusNotConnectedError,
    HassOSError,
    HassOSJobError,
    HassOSSlotNotFound,
    HassOSSlotUpdateError,
    HassOSUpdateAlreadyInstalledError,
    HassOSUpdateError,
    HassOSUpdatePendingRebootError,
    HostError,
)
from ..jobs.const import JobConcurrency, JobCondition
from ..jobs.decorator import Job
from ..jobs.job_group import JobGroup
from ..resolution.const import ContextType, IssueType, SuggestionType
from .data_disk import DataDisk

_LOGGER: logging.Logger = logging.getLogger(__name__)

# GA fork (Odoo #2136, SV-2): the OTA bundle download is resumable and
# size-aware. A fixed 60 min total timeout failed every bundle (~200 MB) on
# links below ~0.5 Mbit/s and threw the partial download away; the update to a
# new OS version on a slow device therefore never finished.
#   - the download goes to <bundle>.part and SURVIVES a failed attempt or a
#     failed update call; the next attempt resumes with HTTP Range + If-Range
#   - an attempt is aborted only on a STALL (no data for OTA_STALL_TIMEOUT);
#     the total budget grows with the size like the image pull limit (SV-1)
#   - attempts are retried with backoff inside one update call
#   - the bundle is checked against the store's <url>.sha256 sidecar when the
#     store has one; without one RAUC's signature check is the only gate
#     (announced at WARNING)
OTA_STALL_TIMEOUT: Final = 300  # seconds without data before an attempt is aborted
OTA_TOTAL_TIMEOUT: Final = 3600  # minimum total budget for one update call
OTA_MIN_RATE: Final = 16 * 1024  # bytes/s the budget assumes at worst
OTA_TOTAL_TIMEOUT_MAX: Final = 24 * 3600  # hard cap whatever the size
OTA_MAX_ATTEMPTS: Final = 8
OTA_BACKOFF: Final = 30  # first pause between attempts, doubled each time
OTA_BACKOFF_MAX: Final = 900
# When a connection drops, aiohttp raises before handing out what its read-ahead
# buffer still holds; those bytes are fetched again on resume (measured: about
# 200 KB per cut, bounded by aiohttp's buffer, not by this read size).
OTA_CHUNK: Final = 64 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def ota_total_budget(size: int | None) -> float:
    """Return the total time budget in seconds for an OTA bundle of size bytes."""
    by_size = size / OTA_MIN_RATE if size else 0
    return min(max(OTA_TOTAL_TIMEOUT, by_size), OTA_TOTAL_TIMEOUT_MAX)


class _OtaRetry(Exception):
    """A transient failure of one download attempt (retried)."""


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


def _unlink(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(OTA_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


# SSH service on Home Assistant OS consuming /root/.ssh/authorized_keys
DROPBEAR_SERVICE = "dropbear.service"

# OS Agent releases before this return the os.Remove error when clearing an
# already absent authorized_keys file (inverted error check)
CLEAR_SSH_AUTH_KEYS_FIXED_VERSION = AwesomeVersion("1.10.0")
CLEAR_SSH_AUTH_KEYS_MISSING_FILE_ERROR = (
    "remove /root/.ssh/authorized_keys: no such file or directory"
)


@dataclass(slots=True, frozen=True)
class SlotStatus:
    """Status of a slot."""

    class_: str
    type_: str
    state: str
    device: PurePath
    bundle_compatible: str | None = None
    sha256: str | None = None
    size: int | None = None
    installed_count: int | None = None
    bundle_version: AwesomeVersion | None = None
    installed_timestamp: datetime | None = None
    status: str | None = None
    activated_count: int | None = None
    activated_timestamp: datetime | None = None
    boot_status: RaucState | None = None
    bootname: str | None = None
    parent: str | None = None

    @classmethod
    def from_dict(cls, data: SlotStatusDataType) -> SlotStatus:
        """Create SlotStatus from dictionary."""
        return cls(
            class_=data["class"],
            type_=data["type"],
            state=data["state"],
            device=PurePath(data["device"]),
            bundle_compatible=data.get("bundle.compatible"),
            sha256=data.get("sha256"),
            size=data.get("size"),
            installed_count=data.get("installed.count"),
            bundle_version=AwesomeVersion(data["bundle.version"])
            if "bundle.version" in data
            else None,
            installed_timestamp=datetime.fromisoformat(data["installed.timestamp"])
            if "installed.timestamp" in data
            else None,
            status=data.get("status"),
            activated_count=data.get("activated.count"),
            activated_timestamp=datetime.fromisoformat(data["activated.timestamp"])
            if "activated.timestamp" in data
            else None,
            boot_status=RaucState(data["boot-status"])
            if "boot-status" in data
            else None,
            bootname=data.get("bootname"),
            parent=data.get("parent"),
        )


class OSManager(JobGroup):
    """OS interface inside supervisor."""

    def __init__(self, coresys: CoreSys):
        """Initialize HassOS handler."""
        super().__init__(coresys, "os_manager")
        self._datadisk: DataDisk = DataDisk(coresys)
        self._available: bool = False
        self._version: AwesomeVersion | None = None
        self._version_pending: AwesomeVersion | None = None
        self._board: str | None = None
        self._os_name: str | None = None
        self._slots: dict[str, SlotStatus] | None = None

    @property
    def available(self) -> bool:
        """Return True, if HassOS on host."""
        return self._available

    @property
    def version(self) -> AwesomeVersion | None:
        """Return version of HassOS."""
        return self._version

    @property
    def latest_version(self) -> AwesomeVersion | None:
        """Return version of HassOS."""
        return self.sys_updater.version_hassos

    @property
    def latest_version_unrestricted(self) -> AwesomeVersion | None:
        """Return current latest version of HassOS for board ignoring upgrade restrictions."""
        return self.sys_updater.version_hassos_unrestricted

    @property
    def version_pending(self) -> AwesomeVersion | None:
        """Return version of an installed update that awaits a reboot to activate."""
        return self._version_pending

    @property
    def need_update(self) -> bool:
        """Return true if a HassOS update is available."""
        try:
            return (
                self.version is not None
                and self.latest_version is not None
                and self.version < self.latest_version
                and (
                    self.version_pending is None
                    or self.latest_version != self.version_pending
                )
            )
        except AwesomeVersionException, TypeError:
            return False

    @property
    def board(self) -> str | None:
        """Return board name."""
        return self._board

    @property
    def os_name(self) -> str | None:
        """Return OS name."""
        return self._os_name

    @property
    def datadisk(self) -> DataDisk:
        """Return Operating-System datadisk."""
        return self._datadisk

    @property
    def slots(self) -> list[SlotStatus]:
        """Return status of slots."""
        if not self._slots:
            return []
        return list(self._slots.values())

    def get_slot_name(self, boot_name: str) -> str:
        """Get slot name from boot name."""
        if not self._slots:
            raise HassOSSlotNotFound

        for name, status in self._slots.items():
            if status.bootname == boot_name:
                return name
        raise HassOSSlotNotFound

    def _get_download_url(self, version: AwesomeVersion) -> str:
        raw_url = self.sys_updater.ota_url
        if raw_url is None:
            raise HassOSUpdateError("Don't have an URL for OTA updates!", _LOGGER.error)

        update_board = self.board
        update_os_name = self.os_name

        # OS version 6 and later renamed intel-nuc to generic-x86-64...
        if update_board == "intel-nuc" and version >= 6.0:
            update_board = "generic-x86-64"

        # The OS name used to be hassos before renaming to haos...
        if version < 6.0:
            update_os_name = "hassos"
        else:
            update_os_name = "haos"

        return raw_url.format(
            version=str(version), board=update_board, os_name=update_os_name
        )

    async def _fetch_ota_sha256(self, url: str) -> str | None:
        """Return the sha256 the store publishes for url, None when it has none."""
        name = url.rsplit("/", 1)[-1]
        try:
            timeout = aiohttp.ClientTimeout(total=120, connect=60)
            async with self.sys_websession.get(f"{url}.sha256", timeout=timeout) as req:
                if req.status != 200:
                    _LOGGER.warning(
                        "No sha256 sidecar for %s (HTTP %s) - relying on the RAUC signature check only",
                        url,
                        req.status,
                    )
                    return None
                text = (await req.text()).strip()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _OtaRetry(f"sha256 sidecar: {err!s}") from err
        parts = text.split()
        if (
            len(parts) < 2
            or not _SHA_RE.match(parts[0].lower())
            or parts[1].lstrip("*").rsplit("/", 1)[-1] != name
        ):
            raise HassOSUpdateError(
                f"Malformed sha256 sidecar for {url}", _LOGGER.error
            )
        return parts[0].lower()

    async def _fetch_ota_attempt(
        self, url: str, part: Path, etag_file: Path, started: float
    ) -> None:
        """One download attempt into part, resuming what part already holds."""
        loop = asyncio.get_running_loop()
        offset = await self.sys_run_in_executor(_file_size, part)
        headers: dict[str, str] = {}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            etag = await self.sys_run_in_executor(_read_text, etag_file)
            if etag.startswith('"') and etag.endswith('"'):
                headers["If-Range"] = etag
        # No total timeout: a stall (no data for OTA_STALL_TIMEOUT) ends the
        # attempt, the size-aware budget below ends the whole download.
        timeout = aiohttp.ClientTimeout(
            total=None, connect=180, sock_read=OTA_STALL_TIMEOUT
        )
        try:
            async with self.sys_websession.get(
                url, headers=headers, timeout=timeout
            ) as request:
                if request.status == 416 and offset:
                    return  # nothing past the partial: it is complete
                if request.status in (404, 410):
                    raise HassOSUpdateError(
                        f"Error raised from OTA Webserver: {request.status}",
                        _LOGGER.error,
                    )
                if request.status not in (200, 206):
                    raise _OtaRetry(f"HTTP {request.status}")
                if request.status == 200:
                    offset = 0  # the server sent the whole file (no or refused range)
                etag = request.headers.get("ETag", "")
                if etag.startswith('"'):
                    await self.sys_run_in_executor(etag_file.write_text, etag)
                total: int | None = None
                content_range = request.headers.get("Content-Range", "")
                if "/" in content_range:
                    tail = content_range.rsplit("/", 1)[1]
                    total = int(tail) if tail.isdigit() else None
                elif request.content_length is not None:
                    total = offset + request.content_length
                budget = ota_total_budget(total)

                ota_file = await self.sys_run_in_executor(
                    part.open, "ab" if offset else "wb"
                )
                try:
                    while True:
                        chunk = await request.content.read(OTA_CHUNK)
                        if not chunk:
                            break
                        await self.sys_run_in_executor(ota_file.write, chunk)
                        if loop.time() - started > budget:
                            raise HassOSUpdateError(
                                f"OTA download of {url} exceeded the total timeout of"
                                f" {budget:.0f}s ({total} bytes, budget assumes"
                                f" >= {OTA_MIN_RATE} B/s)",
                                _LOGGER.error,
                            )
                finally:
                    await self.sys_run_in_executor(ota_file.close)
                have = await self.sys_run_in_executor(_file_size, part)
                if total is not None and have < total:
                    raise _OtaRetry(f"connection closed at {have} of {total} bytes")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _OtaRetry(str(err) or type(err).__name__) from err

    async def _download_raucb(self, url: str, raucb: Path) -> None:
        """Download rauc bundle (OTA) from URL - resumable (GA fork, Odoo #2136)."""
        _LOGGER.info("Fetch OTA update from %s", url)
        part = raucb.with_name(f"{raucb.name}.part")
        etag_file = raucb.with_name(f"{raucb.name}.part.etag")
        sha_file = raucb.with_name(f"{raucb.name}.part.sha256")
        loop = asyncio.get_running_loop()
        started = loop.time()

        def _cleanup_other_partials() -> None:
            for old in raucb.parent.glob("hassos-*.raucb.part*"):
                if not old.name.startswith(f"{raucb.name}.part"):
                    old.unlink(missing_ok=True)

        try:
            await self.sys_run_in_executor(_cleanup_other_partials)
            attempt = 0
            pause = OTA_BACKOFF
            expected: str | None = None
            while True:
                attempt += 1
                try:
                    expected = await self._fetch_ota_sha256(url)
                    # A partial of another build of this version starts over.
                    known = await self.sys_run_in_executor(_read_text, sha_file)
                    have = await self.sys_run_in_executor(_file_size, part)
                    if expected and have and known != expected:
                        _LOGGER.info(
                            "Partial OTA download belongs to another build - starting from 0"
                        )
                        await self.sys_run_in_executor(_unlink, part, etag_file)
                        have = 0
                    if expected:
                        await self.sys_run_in_executor(sha_file.write_text, expected)
                    if have:
                        _LOGGER.info(
                            "Resuming OTA download at byte %d (attempt %d)",
                            have,
                            attempt,
                        )
                    await self._fetch_ota_attempt(url, part, etag_file, started)
                    break
                except _OtaRetry as err:
                    if attempt >= OTA_MAX_ATTEMPTS:
                        self.sys_supervisor.request_connectivity_check()
                        raise HassOSUpdateError(
                            f"Can't fetch OTA update from {url} after {attempt}"
                            f" attempts: {err!s}",
                            _LOGGER.error,
                        ) from err
                    _LOGGER.warning(
                        "OTA download attempt %d/%d failed: %s - retrying in %ds",
                        attempt,
                        OTA_MAX_ATTEMPTS,
                        err,
                        pause,
                    )
                    await asyncio.sleep(pause)
                    pause = min(pause * 2, OTA_BACKOFF_MAX)

            if expected:
                have = await self.sys_run_in_executor(_sha256_file, part)
                if have != expected:
                    await self.sys_run_in_executor(_unlink, part, etag_file)
                    raise HassOSUpdateError(
                        f"OTA bundle sha256 mismatch for {url} (got {have},"
                        f" sidecar {expected}) - partial discarded",
                        _LOGGER.error,
                    )
            await self.sys_run_in_executor(part.replace, raucb)
            await self.sys_run_in_executor(_unlink, etag_file, sha_file)
            _LOGGER.info("Completed download of OTA update file %s", raucb)

        except OSError as err:
            self.sys_resolution.check_oserror(err)
            raise HassOSUpdateError(
                f"Can't write OTA file: {err!s}", _LOGGER.error
            ) from err

    @Job(name="os_manager_reload", conditions=[JobCondition.HAOS], internal=True)
    async def reload(self) -> None:
        """Update cache of slot statuses."""
        self._slots = {
            slot[0]: SlotStatus.from_dict(slot[1])
            for slot in await self.sys_dbus.rauc.get_slot_status()
        }

    async def load(self) -> None:
        """Load HassOS data."""
        try:
            if not self.sys_host.info.cpe:
                raise NotImplementedError

            cpe = CPE(self.sys_host.info.cpe)
            os_name = cpe.get_product()[0]
            if os_name not in ("hassos", "haos"):
                self._board = BOARD_NAME_SUPERVISED.lower()
                raise NotImplementedError
        except NotImplementedError:
            _LOGGER.info("No Home Assistant Operating System found")
            return

        # Store meta data
        self._available = True
        self.sys_host.supported_features.cache_clear()
        self._version = AwesomeVersion(cpe.get_version()[0])
        self._board = cpe.get_target_hardware()[0]
        self._os_name = cpe.get_product()[0]

        await self.reload()

        await self.datadisk.load()

        _LOGGER.info(
            "Detect Home Assistant Operating System %s / BootSlot %s",
            self.version,
            self.sys_dbus.rauc.boot_slot,
        )

    async def _detect_pending_update(self) -> None:
        """Detect an installed update that still requires a reboot to activate.

        A successful rauc install makes the target slot the primary boot slot
        while the old version keeps running until reboot. Supervisor may
        restart in that window, so recover the pending state from rauc.

        Must run after the booted slot was marked good: rauc's GRUB backend
        only treats a slot as primary once it has no boot attempts pending,
        so until the boot attempt counter of the booted slot is reset,
        GetPrimary reports the previous slot as primary on every boot.
        """
        try:
            primary = await self.sys_dbus.rauc.get_primary()
        except DBusError, DBusNotConnectedError:
            _LOGGER.warning("Can't get primary boot slot from rauc")
            return

        if (
            not self._slots
            or (status := self._slots.get(primary)) is None
            or not status.bundle_version
            or not status.bootname
        ):
            return

        # Nothing pending if the primary slot is the booted one or holds
        # the same version as the running system
        if (
            status.bootname == self.sys_dbus.rauc.boot_slot
            or status.bundle_version == self.version
        ):
            return

        self._version_pending = status.bundle_version
        _LOGGER.info(
            "Home Assistant Operating System %s is installed and pending a reboot to activate",
            status.bundle_version,
        )
        self.sys_resolution.create_issue(
            IssueType.REBOOT_REQUIRED,
            ContextType.SYSTEM,
            suggestions=[SuggestionType.EXECUTE_REBOOT],
        )

    @Job(
        name="os_manager_config_sync",
        conditions=[JobCondition.HAOS],
        on_condition=HassOSJobError,
    )
    async def config_sync(self) -> None:
        """Trigger a host config reload from usb."""
        _LOGGER.info(
            "Synchronizing configuration from USB with Home Assistant Operating System."
        )
        # hassos-config.service was renamed to haos-config.service in HAOS 18.0.
        # The unit's Alias= is not reported by systemd's ListUnits, which
        # ServiceManager.exists() relies on, so call the correct unit here.
        if self.version is not None and self.version >= AwesomeVersion("18.0.rc1"):
            service = "haos-config.service"
        else:
            service = "hassos-config.service"
        await self.sys_host.services.restart(service)

    @Job(
        name="os_manager_update",
        conditions=[
            JobCondition.HAOS,
            JobCondition.HEALTHY,
            JobCondition.INTERNET_SYSTEM,
            JobCondition.RUNNING,
            JobCondition.SUPERVISOR_UPDATED,
        ],
        on_condition=HassOSJobError,
        concurrency=JobConcurrency.REJECT,
    )
    async def update(self, version: AwesomeVersion | None = None) -> None:
        """Update HassOS system."""
        version = version or self.latest_version

        # Check installed version
        if not version:
            raise HassOSUpdateError(
                "No version information available, cannot update", _LOGGER.error
            )
        if version == self.version:
            raise HassOSUpdateAlreadyInstalledError(
                _LOGGER.warning, version=str(version)
            )
        if self.version_pending is not None and version == self.version_pending:
            raise HassOSUpdatePendingRebootError(_LOGGER.warning, version=str(version))

        # Fetch files from internet
        ota_url = self._get_download_url(version)
        int_ota = Path(self.sys_config.path_tmp, f"hassos-{version!s}.raucb")
        ext_ota = Path(self.sys_config.path_extern_tmp, int_ota.name)

        try:
            await self._download_raucb(ota_url, int_ota)
            async with self.sys_dbus.rauc.signal_completed() as signal:
                # Start listening for signals before triggering install
                # This prevents a race condition with install complete signal

                await self.sys_dbus.rauc.install(ext_ota)
                completed = await signal.wait_for_signal()

        except DBusError as err:
            raise HassOSUpdateError("Rauc communication error", _LOGGER.error) from err

        finally:
            # Also removes a partial bundle left behind by a failed download
            await self.sys_run_in_executor(partial(int_ota.unlink, missing_ok=True))

        # Update success
        if 0 in completed:
            _LOGGER.info(
                "Install of Home Assistant Operating System %s success; reboot required",
                version,
            )
            self._version_pending = version
            self.sys_resolution.create_issue(
                IssueType.REBOOT_REQUIRED,
                ContextType.SYSTEM,
                suggestions=[SuggestionType.EXECUTE_REBOOT],
            )
            return

        # Update failed
        await self.sys_dbus.rauc.update()
        _LOGGER.error(
            "Home Assistant Operating System update failed with: %s",
            self.sys_dbus.rauc.last_error,
        )
        # The failed install overwrote the target slot, so a previously
        # installed update pending activation is gone as well
        self._version_pending = None
        raise HassOSUpdateError

    @Job(
        name="os_manager_update_raspberrypi_firmware",
        conditions=[JobCondition.HAOS],
        on_condition=HassOSJobError,
        concurrency=JobConcurrency.REJECT,
    )
    async def update_raspberrypi_firmware(self) -> None:
        """Update Raspberry Pi firmware (and VL805 where present).

        Always raises a REBOOT_REQUIRED issue on success — the running
        bootloader is the old one until reboot, regardless of whether the
        update was flashed live (BCM2712) or staged for next boot (BCM2711).
        """
        if not self.sys_dbus.agent.board.has_rpi_firmware:
            raise HassOSJobError(
                "Raspberry Pi firmware is not available on this board",
                _LOGGER.error,
            )

        rpi = self.sys_dbus.agent.board.rpi_firmware
        if rpi.update_blocked:
            raise HassOSJobError(
                "Raspberry Pi firmware update is unavailable on this boot device",
                _LOGGER.warning,
            )

        try:
            await rpi.update_firmware()
        except DBusError as err:
            raise HassOSJobError(
                f"Raspberry Pi firmware update failed: {err}. "
                "Check the Host logs for details.",
                _LOGGER.error,
            ) from err

        _LOGGER.info("Raspberry Pi firmware update completed; reboot required")
        self.sys_resolution.create_issue(
            IssueType.REBOOT_REQUIRED,
            ContextType.SYSTEM,
            suggestions=[SuggestionType.EXECUTE_REBOOT],
        )

    @Job(name="os_manager_mark_healthy", conditions=[JobCondition.HAOS], internal=True)
    async def mark_healthy(self) -> None:
        """Set booted partition as good for rauc."""
        try:
            # Marking the booted slot good resets its boot attempt counter,
            # which the pending update detection below relies on: rauc's GRUB
            # backend only treats a slot as primary once it has no boot
            # attempts pending.
            response = await self.sys_dbus.rauc.mark(RaucState.GOOD, "booted")
        except DBusError:
            _LOGGER.exception("Can't mark booted partition as healthy!")
            return
        _LOGGER.info("Rauc: slot %s - %s", self.sys_dbus.rauc.boot_slot, response[1])

        await self.reload()
        await self._detect_pending_update()

        try:
            # Marking the booted slot as active makes it the primary boot
            # slot again, which would cancel an installed update that still
            # awaits a reboot to activate.
            if not self.version_pending:
                response = await self.sys_dbus.rauc.mark(RaucState.ACTIVE, "booted")
                await self.reload()
                _LOGGER.info(
                    "Rauc: slot %s - %s", self.sys_dbus.rauc.boot_slot, response[1]
                )
        except DBusError:
            _LOGGER.exception("Can't mark booted partition as active!")

    @Job(
        name="os_manager_set_boot_slot",
        conditions=[JobCondition.HAOS],
        on_condition=HassOSJobError,
        internal=True,
    )
    async def set_boot_slot(self, boot_name: str) -> None:
        """Set active boot slot."""
        try:
            response = await self.sys_dbus.rauc.mark(
                RaucState.ACTIVE, self.get_slot_name(boot_name)
            )
        except DBusError as err:
            raise HassOSSlotUpdateError(
                f"Can't mark {boot_name} as active!", _LOGGER.error
            ) from err

        _LOGGER.info("Rauc: %s - %s", self.sys_dbus.rauc.boot_slot, response[1])

        _LOGGER.info("Rebooting into new boot slot now")
        await self.sys_host.control.reboot()

    @Job(
        name="os_manager_add_ssh_authorized_key",
        conditions=[JobCondition.HAOS],
        on_condition=HassOSJobError,
        concurrency=JobConcurrency.GROUP_QUEUE,
        internal=True,
    )
    async def add_ssh_authorized_key(self, key: str) -> None:
        """Add an SSH authorized key for root on the host and start dropbear.

        OS Agent validates the key since 1.10.0; older releases append it to
        the authorized_keys file as submitted.
        """
        _LOGGER.info("Adding SSH authorized key on host")
        try:
            await self.sys_dbus.agent.system.add_ssh_auth_key(key)
        except DBusError as err:
            raise HassOSError(
                f"Can't add SSH authorized key: {err!s}", _LOGGER.error
            ) from err

        # dropbear on Home Assistant OS is gated by
        # ConditionFileNotEmpty=/root/.ssh/authorized_keys, which systemd only
        # evaluates when the unit starts. A running dropbear re-reads the file
        # on every authentication attempt and starting an active unit is a
        # no-op, so only the stopped service needs this.
        try:
            await self.sys_host.services.start(DROPBEAR_SERVICE)
        except (HostError, DBusError) as err:
            raise HassOSError(
                f"SSH authorized key written, but can't start dropbear: {err!s}",
                _LOGGER.error,
            ) from err

    @Job(
        name="os_manager_clear_ssh_authorized_keys",
        conditions=[JobCondition.HAOS],
        on_condition=HassOSJobError,
        concurrency=JobConcurrency.GROUP_QUEUE,
        internal=True,
    )
    async def clear_ssh_authorized_keys(self) -> None:
        """Remove all SSH authorized keys of root on the host and stop dropbear."""
        _LOGGER.info("Clearing SSH authorized keys on host")
        try:
            await self.sys_dbus.agent.system.clear_ssh_auth_keys()
        except DBusError as err:
            # On affected OS Agent releases the missing-file error is the
            # empty state clearing aims for, so treat it as success there.
            if (
                self.sys_dbus.agent.version >= CLEAR_SSH_AUTH_KEYS_FIXED_VERSION
                or CLEAR_SSH_AUTH_KEYS_MISSING_FILE_ERROR not in str(err)
            ):
                raise HassOSError(
                    f"Can't clear SSH authorized keys: {err!s}", _LOGGER.error
                ) from err

        # Mirror the USB config import (haos-config), which stops dropbear
        # when the imported authorized_keys file is removed. Clearing all
        # keys is a revocation, so also terminate established sessions,
        # which survive until the service stops. Stopping an inactive unit
        # is a no-op.
        try:
            await self.sys_host.services.stop(DROPBEAR_SERVICE)
        except (HostError, DBusError) as err:
            raise HassOSError(
                f"SSH authorized keys cleared, but can't stop dropbear: {err!s}",
                _LOGGER.error,
            ) from err
