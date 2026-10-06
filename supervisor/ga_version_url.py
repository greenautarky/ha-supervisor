"""Version URL base read from the host (GA fork).

The OS bind-mounts its /etc/ga-version-url read-only into the Supervisor
container at the same path. The file holds one line: the base URL of the
version feed, ending with '/'. The Supervisor appends "{channel}.json".

Only https URLs inside the GA haos-version repository are accepted. A missing,
unreadable or invalid file falls back to the main branch of that repository
(URL_HASSIO_VERSION) and is reported at WARNING with the reason.
"""

import logging
from pathlib import Path
import re

from .const import URL_HASSIO_VERSION

_LOGGER: logging.Logger = logging.getLogger(__name__)

FILE_GA_VERSION_URL = Path("/etc/ga-version-url")
URL_GA_VERSION_PREFIX = "https://raw.githubusercontent.com/greenautarky/haos-version/"
VERSION_FILE_TEMPLATE = "{channel}.json"

# Everything after the prefix: one or more path segments (branch / directory
# names), each made of safe characters only, ending with '/'. This excludes
# empty segments, query strings, fragments, whitespace and the braces that
# str.format() would interpret.
_REF_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)+")


def _invalid_reason(content: str) -> str | None:
    """Return why the file content is not an acceptable base URL, or None."""
    base = content.strip()
    if not base:
        return "empty"
    if "\n" in base or "\r" in base:
        return "more than one line"
    if not base.lower().startswith("https://"):
        return "not an https URL"
    if not base.startswith(URL_GA_VERSION_PREFIX):
        return f"does not start with prefix {URL_GA_VERSION_PREFIX}"
    ref_path = base[len(URL_GA_VERSION_PREFIX) :]
    if not ref_path:
        return "no branch after the repository prefix"
    if not ref_path.endswith("/"):
        return "does not end with /"
    if not _REF_PATH.fullmatch(ref_path) or any(
        segment in (".", "..") for segment in ref_path.split("/")
    ):
        return f"path {ref_path!r} has an empty, relative or unsafe segment"
    return None


def read_version_url_template(path: Path | None = None) -> str:
    """Return the version URL template, with a {channel} placeholder.

    Does blocking file I/O: call it in an executor.
    """
    url_file = path if path is not None else FILE_GA_VERSION_URL
    try:
        content = url_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        _LOGGER.warning(
            "Version URL file %s is absent, using the default %s",
            url_file,
            URL_HASSIO_VERSION,
        )
        return URL_HASSIO_VERSION
    except (OSError, UnicodeDecodeError) as err:
        _LOGGER.warning(
            "Version URL file %s is unreadable (%s), using the default %s",
            url_file,
            err,
            URL_HASSIO_VERSION,
        )
        return URL_HASSIO_VERSION

    if reason := _invalid_reason(content):
        _LOGGER.warning(
            "Version URL file %s is invalid (%s), using the default %s",
            url_file,
            reason,
            URL_HASSIO_VERSION,
        )
        return URL_HASSIO_VERSION

    template = content.strip() + VERSION_FILE_TEMPLATE
    _LOGGER.info("Using version URL %s from %s", template, url_file)
    return template
