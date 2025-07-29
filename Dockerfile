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
RUN \
    set -x \
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
    && pip3 install uv==0.6.17

# Install requirements
COPY requirements.txt .
RUN \
    if [ "${BUILD_ARCH}" = "i386" ]; then \
        setarch="linux32"; \
    else \
        setarch=""; \
    fi \
    && ${setarch} uv pip install --compile-bytecode --no-cache --no-build -r requirements.txt \
    && rm -f requirements.txt

# Install Home Assistant Supervisor
COPY . supervisor
RUN \
    uv pip install --no-cache -e ./supervisor \
    && python3 -m compileall ./supervisor/supervisor


WORKDIR /
COPY rootfs /
LABEL io.hass.arch="armv7" \
      io.hass.base.arch="armv7" \
      io.hass.base.image="ghcr.io/home-assistant/armv7-base:3.21" \
      io.hass.base.name="python" \
      io.hass.base.version="2025.06.1" \
      io.hass.type="supervisor" \
      io.hass.version="2025.07.2" \
      org.opencontainers.image.authors="The Home Assistant Authors" \
      org.opencontainers.image.created="2025-07-29 02:55:20+0000" \
      org.opencontainers.image.description="Container-based system for managing Home Assistant Core installation" \
      org.opencontainers.image.documentation="https://www.home-assistant.io/docs/" \
      org.opencontainers.image.licenses="Apache License 2.0" \
      org.opencontainers.image.source="https://github.com/iHost-Open-Source-Project/ha-supervisor" \
      org.opencontainers.image.title="Home Assistant Supervisor" \
      org.opencontainers.image.url="https://www.home-assistant.io/" \
      org.opencontainers.image.version="2025.07.2"
