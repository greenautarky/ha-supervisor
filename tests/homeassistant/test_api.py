"""Test Home Assistant API client."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supervisor.coresys import CoreSys


@pytest.mark.usefixtures("websession")
async def test_make_request_sends_path_pre_encoded(coresys: CoreSys):
    """Test make_request sends the path byte-for-byte, without re-decoding.

    yarl normalizes percent-encoded unreserved characters (%5F -> _) unless the
    URL is marked as already encoded. The proxy relies on the bytes it checked
    being the bytes that reach Core (backport of home-assistant/supervisor#7225).
    """
    api = coresys.homeassistant.api
    api_resp = MagicMock(status=200)

    @asynccontextmanager
    async def mock_request(*_args, **_kwargs):
        yield api_resp

    request_mock = MagicMock(side_effect=mock_request)
    coresys.websession.get = request_mock

    with patch.object(api, "ensure_access_token", new_callable=AsyncMock):
        async with api.make_request("get", "api/hassio%5Fauth/a%2Fb") as resp:
            assert resp.status == 200

    url = request_mock.call_args.args[0]
    assert url.raw_path == "/api/hassio%5Fauth/a%2Fb"
    assert str(url) == "http://172.30.32.1:8123/api/hassio%5Fauth/a%2Fb"
