"""Contract test: our API must deserialise with the client Core actually uses.

Home Assistant Core talks to us through `aiohasupervisor`, pinned in
`homeassistant/components/hassio/manifest.json`. Its response models are
mashumaro dataclasses that declare **no defaults**, and Core's `hassio`
coordinator fetches these endpoints in a single `asyncio.gather` during
bootstrap — so one missing or mistyped key is not a degraded field, it stops
Core from finishing its boot at all. The coordinator then retries forever, the
`hassio` config entry never sets up, Supervisor never receives its refresh
token, and the device lands in safe mode with custom components disabled.

Why this exists next to the per-endpoint field-set tests: those compare the
response against a hand-maintained copy of the model's fields. A copy can be
right about the names and still miss a *type* change, and it silently rots the
day upstream adds a field. This test holds the response against the model
itself, so the two sources cannot drift apart unnoticed.
"""

from aiohasupervisor.models import (
    HomeAssistantInfo,
    HostInfo,
    MountsInfo,
    NetworkInfo,
    OSInfo,
    RootInfo,
    StoreInfo,
    SupervisorInfo,
)
from aiohttp.test_utils import TestClient
import pytest

from supervisor.coresys import CoreSys

# The endpoints Core's hassio coordinator gathers at startup, with the model it
# feeds each response into. Pinned deliberately: never derive this from the API
# under test (an audit must not take its expectation from its subject).
CORE_STARTUP_ENDPOINTS = [
    ("/info", RootInfo),
    ("/supervisor/info", SupervisorInfo),
    ("/core/info", HomeAssistantInfo),
    ("/os/info", OSInfo),
    ("/host/info", HostInfo),
    ("/network/info", NetworkInfo),
    ("/store", StoreInfo),
    ("/mounts", MountsInfo),
]


@pytest.fixture(name="host_disk_info", autouse=True)
def fixture_host_disk_info(coresys: CoreSys):
    """Give /host/info a disk to report on.

    The host's real disk is not reachable from the test container, so
    /host/info answers 500 without this. It mocks the ENVIRONMENT, not the
    subject: what is asserted here is the shape of the response, never these
    numbers.
    """

    async def _disk_life_time(_):
        return 0

    coresys.hardware.disk.get_disk_life_time = _disk_life_time
    coresys.hardware.disk.get_disk_free_space = lambda _: 5000
    coresys.hardware.disk.get_disk_total_space = lambda _: 50000
    coresys.hardware.disk.get_disk_used_space = lambda _: 45000
    return coresys


@pytest.mark.parametrize(("path", "model"), CORE_STARTUP_ENDPOINTS)
async def test_core_startup_endpoint_deserialises(
    api_client: TestClient, path: str, model: type
):
    """Every startup endpoint must deserialise into the model Core expects."""
    resp = await api_client.get(path)
    assert resp.status == 200, f"{path} did not answer 200"
    result = await resp.json()

    try:
        model.from_dict(result["data"])
    # pylint: disable-next=broad-exception-caught
    except Exception as err:  # noqa: BLE001
        # Deliberately broad, and narrowing it would weaken the test: mashumaro
        # raises MissingField, InvalidFieldValue and others, and a type we did
        # NOT anticipate is exactly the case worth catching. The failure text is
        # the finding, so it is reported rather than classified.
        pytest.fail(f"{path} is not readable by {model.__name__}: {root_cause(err)}")


def root_cause(err: BaseException) -> str:
    """Name the innermost failure.

    mashumaro reports a nested problem as InvalidFieldValue on the *container*
    ("field interfaces ... has invalid value [<the entire list>]"), which buries
    the missing key under a wall of payload. The finding is in __cause__, and it
    is the part that names the field to add.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = err
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    inner = chain[-1]
    text = str(inner)
    if len(text) > 300:
        text = f"{text[:300]} …"
    return f"{type(inner).__name__}: {text}"


def test_every_startup_endpoint_is_covered():
    """Fail closed if the endpoint table is emptied or trimmed.

    A parametrised suite that runs over zero cases passes silently; assert the
    count so shrinking the table is a failure, not a quiet loss of coverage.
    """
    assert len(CORE_STARTUP_ENDPOINTS) == 8
    assert len({path for path, _ in CORE_STARTUP_ENDPOINTS}) == 8
