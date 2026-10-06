"""Test the configurable version URL (GA fork).

The Supervisor reads the base of its version URL from /etc/ga-version-url at
startup and appends "{channel}.json". Anything missing or not under the GA
haos-version repository falls back to the main branch with a warning.
"""

import logging
from pathlib import Path

import pytest

from supervisor.ga_version_url import FILE_GA_VERSION_URL, read_version_url_template

# Pinned literals. Never derive these from supervisor.const: a wrong constant
# would then make the test agree with it.
MAIN_TEMPLATE = (
    "https://raw.githubusercontent.com/greenautarky/haos-version/main/{channel}.json"
)
CANDIDATE_BASE = (
    "https://raw.githubusercontent.com/greenautarky/haos-version/candidate/stable-1.4/"
)


def test_version_url_file_path():
    """The OS bind-mounts the host file to this path inside the container."""
    assert Path("/etc/ga-version-url") == FILE_GA_VERSION_URL


@pytest.mark.parametrize(
    "content",
    [
        CANDIDATE_BASE,
        CANDIDATE_BASE + "\n",
        "  " + CANDIDATE_BASE + "\r\n",
        "https://raw.githubusercontent.com/greenautarky/haos-version/main/",
        "https://raw.githubusercontent.com/greenautarky/haos-version/release/v1.2-rebuild/",
    ],
)
def test_valid_file_is_used(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
):
    """A valid base URL from the file is used and the channel file appended."""
    url_file = tmp_path / "ga-version-url"
    url_file.write_text(content)

    with caplog.at_level(logging.INFO):
        template = read_version_url_template(url_file)

    expected_base = content.strip()
    assert template == expected_base + "{channel}.json"
    assert template.format(channel="stable") == expected_base + "stable.json"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_absent_file_falls_back_to_main(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """No file: use main and say so at WARNING, naming the reason."""
    url_file = tmp_path / "missing"

    template = read_version_url_template(url_file)

    assert template == MAIN_TEMPLATE
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "absent" in warnings[0].getMessage()
    assert str(url_file) in warnings[0].getMessage()


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("", "empty"),
        ("\n", "empty"),
        (
            "http://raw.githubusercontent.com/greenautarky/haos-version/main/",
            "https",
        ),
        ("ftp://raw.githubusercontent.com/greenautarky/haos-version/main/", "https"),
        ("https://example.com/greenautarky/haos-version/main/", "prefix"),
        ("https://raw.githubusercontent.com/someone/haos-version/main/", "prefix"),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version-evil/main/",
            "prefix",
        ),
        (
            "https://raw.githubusercontent.com.evil.example/greenautarky/haos-version/main/",
            "prefix",
        ),
        (
            "https://user@raw.githubusercontent.com/greenautarky/haos-version/main/",
            "prefix",
        ),
        ("https://raw.githubusercontent.com/greenautarky/haos-version/", "branch"),
        ("https://raw.githubusercontent.com/greenautarky/haos-version/main", "/"),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version/../other/main/",
            "path",
        ),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version/main//",
            "path",
        ),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version/main/?x=/",
            "path",
        ),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version/{channel}/",
            "path",
        ),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version/ma in/",
            "path",
        ),
        (
            "https://raw.githubusercontent.com/greenautarky/haos-version/main/\n"
            "https://raw.githubusercontent.com/greenautarky/haos-version/dev/\n",
            "one line",
        ),
    ],
)
def test_invalid_file_falls_back_to_main(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str, reason: str
):
    """Anything that is not an https URL under the GA feed repo falls back."""
    url_file = tmp_path / "ga-version-url"
    url_file.write_text(content)

    template = read_version_url_template(url_file)

    assert template == MAIN_TEMPLATE
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "invalid" in message
    assert reason in message
    assert str(url_file) in message


def test_unreadable_file_falls_back_to_main(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """A path that cannot be read as text (here: a directory) falls back."""
    template = read_version_url_template(tmp_path)

    assert template == MAIN_TEMPLATE
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "unreadable" in warnings[0].getMessage()
