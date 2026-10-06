"""Validate functions."""

import voluptuous as vol

from ..const import ATTR_ACCESS_TOKEN, ATTR_IMAGE, ATTR_SERVERS, ATTR_VERSION
from ..validate import dns_server_list, docker_image, token, version_tag
from .const import ATTR_FALLBACK, GA_DEFAULT_DNS_SERVERS

# pylint: disable=no-value-for-parameter
SCHEMA_DNS_CONFIG = vol.Schema(
    {
        vol.Optional(ATTR_VERSION): version_tag,
        vol.Optional(ATTR_IMAGE): docker_image,
        # GA defaults: explicit Cloudflare upstreams + DoT fallback OFF.
        # Rationale: with the upstream defaults (servers=[], fallback=True), HA Core's
        # PTR sweeps for the local subnet escalate to DoT (1.1.1.1:853) when the
        # DHCP-derived locals DNS doesn't answer RFC1918 PTRs (typical on captive
        # WiFi / hotspot setups). The DoT TLS handshake hangs ~75-95s per query and
        # spikes hassio_dns to 180% CPU. See ha-flasher-py TODO §"DNS Fallback CPU Spike".
        vol.Optional(
            ATTR_SERVERS, default=lambda: list(GA_DEFAULT_DNS_SERVERS)
        ): dns_server_list,
        vol.Optional(ATTR_FALLBACK, default=False): vol.Boolean(),
    },
    extra=vol.REMOVE_EXTRA,
)


SCHEMA_AUDIO_CONFIG = vol.Schema(
    {vol.Optional(ATTR_VERSION): version_tag, vol.Optional(ATTR_IMAGE): docker_image},
    extra=vol.REMOVE_EXTRA,
)


SCHEMA_CLI_CONFIG = vol.Schema(
    {
        vol.Optional(ATTR_VERSION): version_tag,
        vol.Optional(ATTR_IMAGE): docker_image,
        vol.Optional(ATTR_ACCESS_TOKEN): token,
    },
    extra=vol.REMOVE_EXTRA,
)


SCHEMA_MULTICAST_CONFIG = vol.Schema(
    {vol.Optional(ATTR_VERSION): version_tag, vol.Optional(ATTR_IMAGE): docker_image},
    extra=vol.REMOVE_EXTRA,
)


SCHEMA_OBSERVER_CONFIG = vol.Schema(
    {
        vol.Optional(ATTR_VERSION): version_tag,
        vol.Optional(ATTR_IMAGE): docker_image,
        vol.Optional(ATTR_ACCESS_TOKEN): token,
    },
    extra=vol.REMOVE_EXTRA,
)
