ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.24-2026.10.3

# GA fork: armv7 support (ADR-0016). Upstream publishes no armv7 base and no
# armv7 wheels for Python 3.14, so for armv7 only:
#   * BUILD_FROM is the GA-owned Alpine base (s6-overlay, bashio, tempio),
#     which carries no Python: GA_INSTALL_PYTHON=true installs it from apk.
#   * GA_WHEELS_STAGE=ga-wheels-armv7 compiles, in a throwaway stage that
#     carries the toolchain, every requirement that has no musllinux armv7l
#     wheel (brotli, cryptography, orjson, PyYAML, dbus-fast, ciso8601 ...).
#     Only the finished wheels reach the final image.
# aarch64/amd64 use the defaults: GA_WHEELS_STAGE=ga-wheels-none is an empty
# directory, so their build is the upstream recipe plus the pins below.
ARG GA_WHEELS_STAGE=ga-wheels-none

FROM scratch AS ga-wheels-none
WORKDIR /wheels

FROM ${BUILD_FROM} AS ga-wheels-armv7
ENV PIP_BREAK_SYSTEM_PACKAGES=1
# hadolint ignore=DL3018
RUN apk add --no-cache python3 py3-pip build-base python3-dev cargo rust \
        libffi-dev openssl-dev yaml-dev cmake
COPY requirements.txt /usr/src/requirements.txt
RUN pip3 wheel --wheel-dir /wheels -r /usr/src/requirements.txt

FROM ${GA_WHEELS_STAGE} AS ga-wheels

FROM ${BUILD_FROM} AS supervisor-base

ENV \
    S6_SERVICES_GRACETIME=415000 \
    S6_KILL_GRACETIME=3000 \
    SUPERVISOR_API=http://localhost \
    CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1 \
    UV_SYSTEM_PYTHON=true \
    UV_BREAK_SYSTEM_PACKAGES=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

ARG GA_INSTALL_PYTHON=false

# Install OS packages used both by build and final image
# GA fork: `apk upgrade` first - BUILD_FROM is a dated tag that is not rebuilt,
# so its packages age while Alpine keeps publishing fixes for the same branch.
# (hadolint DL3017 suppressed for this instruction only, as on the 2025.11 line.)
# hadolint ignore=DL3017
RUN \
    set -x \
    && apk --no-cache upgrade \
    && if [ "${GA_INSTALL_PYTHON}" = "true" ]; then \
        apk add --no-cache python3 py3-pip; \
    fi \
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
    && pip3 install uv==0.13.5

# GA fork: the armv7 BUILD_FROM is the GA add-on base, whose s6-overlay carries
# the ADD-ON boot oneshots base-addon-banner and base-addon-log-level. They
# query the Supervisor API for the add-on's own config; inside the Supervisor
# image there is no such API yet (this container IS the API), so they fail
# ("Unknown log_level") and s6 stops the container. Upstream base-python has no
# such services (aarch64 is unaffected, the loop below finds nothing). Remove
# them and every reference, so s6-rc compiles a valid, add-on-free tree.
# Guarded by scripts/image-layout-check.sh in CI.
RUN \
    S6=/package/admin/s6-overlay/etc/s6-rc/sources \
    && for svc in base-addon-banner base-addon-log-level; do \
        rm -rf "${S6}/${svc}" "/package/admin/s6-overlay/etc/s6-rc/scripts/${svc}"; \
        find "${S6}" -name "${svc}" -exec rm -rf {} +; \
    done \
    && ! grep -rl base-addon "${S6}" /package/admin/s6-overlay/etc/s6-rc/scripts

#############################################
# Install requirements and build Supervisor #
#############################################

FROM supervisor-base AS supervisor-build

WORKDIR /usr/src

# Install requirements
RUN \
    --mount=type=bind,source=./requirements.txt,target=/usr/src/requirements.txt \
    --mount=type=bind,source=./wheels,target=/usr/src/wheels \
    --mount=type=bind,from=ga-wheels,source=/wheels,target=/usr/src/ga-wheels \
    if ls /usr/src/wheels/musllinux/* >/dev/null 2>&1; then \
        LOCAL_WHEELS=/usr/src/wheels/musllinux; \
        echo "Using local wheels from: $LOCAL_WHEELS"; \
    else \
        LOCAL_WHEELS=; \
        echo "No local wheels found"; \
    fi && \
    uv pip install --compile-bytecode --no-cache --no-build \
        -r requirements.txt \
        ${LOCAL_WHEELS:+--find-links $LOCAL_WHEELS} \
        --find-links /usr/src/ga-wheels

# Install Home Assistant Supervisor
ARG BUILD_VERSION="9999.09.9.dev9999"
COPY . supervisor
RUN \
    --mount=type=bind,source=./wheels,target=/usr/src/wheels \
    if ls /usr/src/wheels/musllinux/* >/dev/null 2>&1; then \
        LOCAL_WHEELS=/usr/src/wheels/musllinux; \
        echo "Using local wheels from: $LOCAL_WHEELS"; \
    else \
        LOCAL_WHEELS=; \
        echo "No local wheels found"; \
    fi \
    && sed -i "s/^SUPERVISOR_VERSION =.*/SUPERVISOR_VERSION = \"${BUILD_VERSION}\"/g" /usr/src/supervisor/supervisor/const.py \
    && uv pip install --no-cache -e ./supervisor \
        ${LOCAL_WHEELS:+--find-links $LOCAL_WHEELS} \
    && python3 -m compileall ./supervisor/supervisor

# Copy the rest of rootfs files
COPY rootfs /

#########################
# Final flattened image #
#########################

FROM supervisor-base

# Copy everything from the build stage as a single layer
# hadolint ignore=DL3067
COPY --from=supervisor-build / /

LABEL \
    io.hass.type="supervisor" \
    org.opencontainers.image.title="Home Assistant Supervisor" \
    org.opencontainers.image.description="Container-based system for managing Home Assistant Core installation" \
    org.opencontainers.image.authors="The Home Assistant Authors" \
    org.opencontainers.image.url="https://www.home-assistant.io/" \
    org.opencontainers.image.documentation="https://www.home-assistant.io/docs/" \
    org.opencontainers.image.licenses="Apache License 2.0"
