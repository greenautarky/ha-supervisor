"""Const for plugins."""

from datetime import timedelta
from pathlib import Path

from ..const import SUPERVISOR_DATA
from ..jobs.const import JobCondition

FILE_HASSIO_AUDIO = Path(SUPERVISOR_DATA, "audio.json")
FILE_HASSIO_CLI = Path(SUPERVISOR_DATA, "cli.json")
FILE_HASSIO_DNS = Path(SUPERVISOR_DATA, "dns.json")
FILE_HASSIO_OBSERVER = Path(SUPERVISOR_DATA, "observer.json")
FILE_HASSIO_MULTICAST = Path(SUPERVISOR_DATA, "multicast.json")

ATTR_FALLBACK = "fallback"

# GA defaults for hassio_dns plugin. Used by both the schema (initial value when
# no persisted dns.json exists) and reset() (factory-reset state). Keep both in sync.
GA_DEFAULT_DNS_SERVERS = ("dns://1.1.1.1", "dns://1.0.0.1")
GA_DEFAULT_DNS_FALLBACK = False

WATCHDOG_RETRY_SECONDS = 10
WATCHDOG_MAX_ATTEMPTS = 5
WATCHDOG_THROTTLE_PERIOD = timedelta(minutes=30)
WATCHDOG_THROTTLE_MAX_CALLS = 10

PLUGIN_UPDATE_CONDITIONS = [
    JobCondition.FREE_SPACE,
    JobCondition.HEALTHY,
    JobCondition.INTERNET_HOST,
    JobCondition.SUPERVISOR_UPDATED,
    JobCondition.ARCHITECTURE_SUPPORTED,
]
