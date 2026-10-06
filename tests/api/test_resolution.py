"""Test Resolution API."""

from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient
import pytest

from supervisor.const import (
    ATTR_ISSUES,
    ATTR_SUGGESTIONS,
    ATTR_UNHEALTHY,
    ATTR_UNSUPPORTED,
    CoreState,
)
from supervisor.coresys import CoreSys
from supervisor.exceptions import ResolutionError
from supervisor.resolution.const import (
    ContextType,
    IssueType,
    SuggestionType,
    UnhealthyReason,
    UnsupportedReason,
)
from supervisor.resolution.data import Issue, Suggestion


@pytest.mark.asyncio
async def test_api_resolution_base(coresys: CoreSys, api_client: TestClient):
    """Test resolution manager api."""
    coresys.resolution.add_unsupported_reason(UnsupportedReason.OS)
    coresys.resolution.add_suggestion(
        Suggestion(SuggestionType.CLEAR_FULL_BACKUP, ContextType.SYSTEM)
    )
    coresys.resolution.create_issue(IssueType.FREE_SPACE, ContextType.SYSTEM)

    resp = await api_client.get("/resolution/info")
    result = await resp.json()
    assert UnsupportedReason.OS in result["data"][ATTR_UNSUPPORTED]
    assert (
        result["data"][ATTR_SUGGESTIONS][-1]["type"] == SuggestionType.CLEAR_FULL_BACKUP
    )
    assert result["data"][ATTR_ISSUES][-1]["type"] == IssueType.FREE_SPACE


@pytest.mark.asyncio
async def test_api_resolution_dismiss_suggestion(
    coresys: CoreSys, api_client: TestClient
):
    """Test resolution manager suggestion apply api."""
    coresys.resolution.add_suggestion(
        clear_backup := Suggestion(SuggestionType.CLEAR_FULL_BACKUP, ContextType.SYSTEM)
    )

    assert coresys.resolution.suggestions[-1].type == SuggestionType.CLEAR_FULL_BACKUP
    await api_client.delete(f"/resolution/suggestion/{clear_backup.uuid}")
    assert clear_backup not in coresys.resolution.suggestions


@pytest.mark.asyncio
async def test_api_resolution_apply_suggestion(
    coresys: CoreSys, api_client: TestClient
):
    """Test resolution manager suggestion apply api."""
    coresys.resolution.add_suggestion(
        clear_backup := Suggestion(SuggestionType.CLEAR_FULL_BACKUP, ContextType.SYSTEM)
    )
    coresys.resolution.add_suggestion(
        create_backup := Suggestion(
            SuggestionType.CREATE_FULL_BACKUP, ContextType.SYSTEM
        )
    )

    mock_backups = AsyncMock()
    mock_health = AsyncMock()
    coresys.backups.do_backup_full = mock_backups
    coresys.resolution.healthcheck = mock_health

    await api_client.post(f"/resolution/suggestion/{clear_backup.uuid}")
    await api_client.post(f"/resolution/suggestion/{create_backup.uuid}")

    assert clear_backup not in coresys.resolution.suggestions
    assert create_backup not in coresys.resolution.suggestions

    assert mock_backups.called
    assert mock_health.called

    with pytest.raises(ResolutionError):
        await coresys.resolution.apply_suggestion(clear_backup)


@pytest.mark.asyncio
async def test_api_resolution_dismiss_issue(coresys: CoreSys, api_client: TestClient):
    """Test resolution manager issue apply api."""
    coresys.resolution.add_issue(
        updated_failed := Issue(IssueType.UPDATE_FAILED, ContextType.SYSTEM)
    )

    assert coresys.resolution.issues[-1].type == IssueType.UPDATE_FAILED
    await api_client.delete(f"/resolution/issue/{updated_failed.uuid}")
    assert updated_failed not in coresys.resolution.issues


@pytest.mark.asyncio
async def test_api_resolution_unhealthy(coresys: CoreSys, api_client: TestClient):
    """Test resolution manager api."""
    coresys.resolution.add_unhealthy_reason(UnhealthyReason.DOCKER)

    resp = await api_client.get("/resolution/info")
    result = await resp.json()
    assert result["data"][ATTR_UNHEALTHY][-1] == UnhealthyReason.DOCKER


@pytest.mark.asyncio
async def test_api_resolution_check_options(coresys: CoreSys, api_client: TestClient):
    """Test client API with checks options."""
    free_space = coresys.resolution.check.get("free_space")

    assert free_space.enabled
    await api_client.post(
        f"/resolution/check/{free_space.slug}/options", json={"enabled": False}
    )
    assert not free_space.enabled

    await api_client.post(
        f"/resolution/check/{free_space.slug}/options", json={"enabled": True}
    )
    assert free_space.enabled


@pytest.mark.asyncio
async def test_api_resolution_check_run(coresys: CoreSys, api_client: TestClient):
    """Test client API with run check."""
    await coresys.core.set_state(CoreState.RUNNING)
    free_space = coresys.resolution.check.get("free_space")

    free_space.run_check = AsyncMock()

    await api_client.post(f"/resolution/check/{free_space.slug}/run")

    assert free_space.run_check.called


async def test_api_resolution_suggestions_for_issue(
    coresys: CoreSys, api_client: TestClient
):
    """Test getting suggestions that fix an issue."""
    coresys.resolution.add_issue(
        corrupt_repo := Issue(IssueType.CORRUPT_REPOSITORY, ContextType.STORE, "repo_1")
    )

    resp = await api_client.get(f"/resolution/issue/{corrupt_repo.uuid}/suggestions")
    result = await resp.json()

    assert result["data"]["suggestions"] == []

    coresys.resolution.add_suggestion(
        execute_reset := Suggestion(
            SuggestionType.EXECUTE_RESET, ContextType.STORE, "repo_1"
        )
    )
    coresys.resolution.add_suggestion(
        execute_remove := Suggestion(
            SuggestionType.EXECUTE_REMOVE, ContextType.STORE, "repo_1"
        )
    )

    resp = await api_client.get(f"/resolution/issue/{corrupt_repo.uuid}/suggestions")
    result = await resp.json()

    suggestion = [
        su for su in result["data"]["suggestions"] if su["uuid"] == execute_reset.uuid
    ]
    assert len(suggestion) == 1
    assert suggestion[0]["auto"] is True

    suggestion = [
        su for su in result["data"]["suggestions"] if su["uuid"] == execute_remove.uuid
    ]
    assert len(suggestion) == 1
    assert suggestion[0]["auto"] is False


@pytest.mark.parametrize(
    ("method", "url"),
    [("delete", "/resolution/issue/bad"), ("get", "/resolution/issue/bad/suggestions")],
)
async def test_issue_not_found(api_client: TestClient, method: str, url: str):
    """Test issue not found error."""
    resp = await api_client.request(method, url)
    assert resp.status == 404
    body = await resp.json()
    assert body["message"] == "The supplied UUID is not a valid issue"


@pytest.mark.parametrize(
    ("method", "url"),
    [("delete", "/resolution/suggestion/bad"), ("post", "/resolution/suggestion/bad")],
)
async def test_suggestion_not_found(api_client: TestClient, method: str, url: str):
    """Test suggestion not found error."""
    resp = await api_client.request(method, url)
    assert resp.status == 404
    body = await resp.json()
    assert body["message"] == "The supplied UUID is not a valid suggestion"


@pytest.mark.parametrize(
    ("method", "url"),
    [("post", "/resolution/check/bad/options"), ("post", "/resolution/check/bad/run")],
)
async def test_check_not_found(api_client: TestClient, method: str, url: str):
    """Test check not found error."""
    resp = await api_client.request(method, url)
    assert resp.status == 404
    body = await resp.json()
    assert body["message"] == "The supplied check slug is not available"


# Fields required by aiohasupervisor 0.6.0 (the client Home Assistant Core
# 2026.8.2 pins) for the models served by /resolution/info. Every field of
# these mashumaro dataclasses is required — none carries a default — so a
# missing key raises MissingField and stalls Core's bootstrap.
#
# Pinned constants, never derived from the response under test.
#
# These lists MUST be exercised against a non-empty resolution state: with
# zero issues and zero suggestions the endpoint deserializes fine even when
# the per-entry fields are missing, which is exactly how this gap survived
# the earlier contract tests.
CORE_2026_REQUIRED_RESOLUTION_INFO_FIELDS = {
    "checks",
    "issues",
    "suggestions",
    "unhealthy",
    "unsupported",
}
CORE_2026_REQUIRED_ISSUE_FIELDS = {
    "context",
    "reference",
    "reference_extra",
    "type",
    "uuid",
}
CORE_2026_REQUIRED_SUGGESTION_FIELDS = {
    "auto",
    "context",
    "reference",
    "reference_extra",
    "type",
    "uuid",
}
CORE_2026_REQUIRED_CHECK_FIELDS = {"enabled", "slug"}


async def test_api_resolution_info_serves_core_2026_contract(
    coresys: CoreSys, api_client: TestClient
):
    """/resolution/info must serve every field aiohasupervisor 0.6.0 requires."""
    coresys.resolution.add_unsupported_reason(UnsupportedReason.OS)
    coresys.resolution.add_unhealthy_reason(UnhealthyReason.DOCKER)
    # An issue that carries suggestions, so both lists are populated the way
    # they are on a device that actually has something to report.
    coresys.resolution.create_issue(
        IssueType.FREE_SPACE,
        ContextType.SYSTEM,
        suggestions=[SuggestionType.CLEAR_FULL_BACKUP],
    )

    resp = await api_client.get("/resolution/info")
    result = await resp.json()
    data = result["data"]

    missing = CORE_2026_REQUIRED_RESOLUTION_INFO_FIELDS - set(data)
    assert not missing, f"ResolutionInfo fields missing from response: {missing}"

    # Fail closed on an empty fixture: a run over zero issues or zero
    # suggestions inspects nothing and proves nothing.
    assert data["issues"], "no issue in response - the contract check inspected nothing"
    assert data["suggestions"], (
        "no suggestion in response - the contract check inspected nothing"
    )
    assert data["checks"], "no check in response - the contract check inspected nothing"

    for issue in data["issues"]:
        missing = CORE_2026_REQUIRED_ISSUE_FIELDS - set(issue)
        assert not missing, f"Issue fields missing from response: {missing}"
    for suggestion in data["suggestions"]:
        missing = CORE_2026_REQUIRED_SUGGESTION_FIELDS - set(suggestion)
        assert not missing, f"Suggestion fields missing from response: {missing}"
    for check in data["checks"]:
        missing = CORE_2026_REQUIRED_CHECK_FIELDS - set(check)
        assert not missing, f"Check fields missing from response: {missing}"

    # Core reads the same Suggestion model from this endpoint, once per issue
    # it received above.
    resp = await api_client.get(
        f"/resolution/issue/{data['issues'][0]['uuid']}/suggestions"
    )
    suggestions = (await resp.json())["data"]["suggestions"]
    assert suggestions, (
        "no suggestion for the issue - the contract check inspected nothing"
    )
    for suggestion in suggestions:
        missing = CORE_2026_REQUIRED_SUGGESTION_FIELDS - set(suggestion)
        assert not missing, (
            f"Suggestion fields missing from suggestions_for_issue: {missing}"
        )


async def test_api_resolution_info_reference_extra_is_a_real_value(
    coresys: CoreSys, api_client: TestClient
):
    """reference_extra carries the real payload, it is not a hardcoded None.

    Also pins the inheritance upstream #6916 introduced: suggestions created
    from an issue take that issue's reference_extra.
    """
    coresys.resolution.create_issue(
        IssueType.FREE_SPACE,
        ContextType.SYSTEM,
        reference="test_reference",
        suggestions=[SuggestionType.CLEAR_FULL_BACKUP],
        reference_extra={"detail": 42},
    )

    resp = await api_client.get("/resolution/info")
    data = (await resp.json())["data"]

    issue = next(i for i in data["issues"] if i["reference"] == "test_reference")
    assert issue["reference_extra"] == {"detail": 42}

    suggestion = next(
        s for s in data["suggestions"] if s["reference"] == "test_reference"
    )
    assert suggestion["reference_extra"] == {"detail": 42}


async def test_api_resolution_info_reference_extra_defaults_to_none(
    coresys: CoreSys, api_client: TestClient
):
    """An issue raised without extra detail reports None, not a stub value.

    Must-pass fixture: nothing in this fork populates reference_extra today,
    and the field must not invent one.
    """
    coresys.resolution.create_issue(
        IssueType.FREE_SPACE,
        ContextType.SYSTEM,
        suggestions=[SuggestionType.CLEAR_FULL_BACKUP],
    )

    resp = await api_client.get("/resolution/info")
    data = (await resp.json())["data"]

    assert data["issues"][-1]["reference_extra"] is None
    assert data["suggestions"][-1]["reference_extra"] is None
