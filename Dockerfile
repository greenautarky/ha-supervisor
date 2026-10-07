ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV \
    S6_SERVICES_GRACETIME=10000 \
    SUPERVISOR_API=http://localhost \
    CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1 \
    UV_SYSTEM_PYTHON=true

ARG \
    COSIGN_VERSION \
    BUILD_ARCH \
    QEMU_CPU

# Install base
WORKDIR /usr/src
# `apk upgrade` before `apk add`: BUILD_FROM is a dated upstream tag that is not
# rebuilt, so its packages age while Alpine keeps publishing fixes for the very
# same 3.22 branch. Every finding this clears already had a fix waiting in 3.22;
# none of them needed a newer base, which is what made the first analysis of
# this look like a dead end. Upgrading here also keeps clearing them, which a
# version pin cannot: pins age and nobody is reminded to bump them.
#
# The cost is that two builds of one commit are no longer bit-identical. What
# was actually shipped is recorded in the per-release SBOM, not in a pin.
#
# Measurement and the pinned-versions alternative that was weighed against this:
# internal tracker, task 713.
#
# DL3017 ("do not use apk upgrade") assumes a base that its own maintainer keeps
# current. That assumption does not hold here — the pinned upstream tag is a
# frozen snapshot — so the rule is suppressed for this instruction only, never
# file-wide, so that it keeps guarding any RUN added later.
# hadolint ignore=DL3017
RUN \
    set -x \
    && apk --no-cache upgrade \
    && apk add --no-cache \
        findutils \
        eudev \
        eudev-libs \
        git \
        libffi \
        libpulse \
        musl \
        openssl \
        yaml \
    \
    && curl -Lso /usr/bin/cosign "https://github.com/home-assistant/cosign/releases/download/${COSIGN_VERSION}/cosign_${BUILD_ARCH}" \
    && chmod a+x /usr/bin/cosign \
    && pip3 install uv==0.8.9

# tempio from its current release (the base image carries an older build that
# nothing in this image calls, but it ships, so it is kept current rather than
# removed from what the base provides). Checksum-pinned; only the armv7 binary
# is pinned because only armv7 is built from this fork.
ARG TEMPIO_VERSION=2026.07.0
ARG TEMPIO_SHA256=1887c4721317ee166de703ddb30f906c9f98f01f08a6b2da295c10946a0c8110
RUN \
    curl -Lfso /usr/bin/tempio "https://github.com/home-assistant/tempio/releases/download/${TEMPIO_VERSION}/tempio_${BUILD_ARCH}" \
    && echo "${TEMPIO_SHA256}  /usr/bin/tempio" > /tmp/tempio.sha256 \
    && sha256sum -c /tmp/tempio.sha256 \
    && rm -f /tmp/tempio.sha256 \
    && chmod a+x /usr/bin/tempio

# Install requirements
#
# --index-strategy unsafe-best-match: the base image sets UV_EXTRA_INDEX_URL to
# the Home Assistant musllinux wheel index, and uv's default strategy takes a
# package only from the FIRST index that lists it. That index has no cp313
# armv7 wheels after aiohttp 3.13.3 and orjson 3.11.4 (aiohttp 3.14.3 and
# orjson 3.11.9 are listed there for cp314 aarch64/x86_64 only, measured
# 2026-10-07), so those pins are unsatisfiable under --no-build although PyPI
# ships the cp313 musllinux armv7l wheels. Best-match picks the requested version from whichever index has a
# matching wheel. Every direct dependency is pinned to an exact version.
COPY requirements.txt .
RUN \
    if [ "${BUILD_ARCH}" = "i386" ]; then \
        setarch="linux32"; \
    else \
        setarch=""; \
    fi \
    && ${setarch} uv pip install --compile-bytecode --no-cache --no-build \
        --index-strategy unsafe-best-match -r requirements.txt \
    && rm -f requirements.txt

# Install Home Assistant Supervisor
COPY . supervisor
RUN \
    uv pip install --no-cache -e ./supervisor \
    && python3 -m compileall ./supervisor/supervisor


WORKDIR /
COPY rootfs /
