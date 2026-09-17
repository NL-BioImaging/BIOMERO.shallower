# Build from this repository; schema contracts come from the published package.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS wheels
WORKDIR /build
RUN pip install --no-cache-dir setuptools==80.9.0 wheel==0.45.1 packaging==26.3
COPY pyproject.toml README.md LICENSE /build/shallower/
COPY biomero_shallower /build/shallower/biomero_shallower
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /build/shallower

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
LABEL org.opencontainers.image.title="BIOMERO.shallower" \
      org.opencontainers.image.source="https://github.com/NL-BioImaging/BIOMERO.shallower" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.1.0"
COPY requirements.lock /tmp/requirements.lock
COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir -r /tmp/requirements.lock /wheels/*.whl \
    && pip check \
    && rm -rf /wheels /tmp/requirements.lock \
    && useradd --uid 10001 --create-home shallower
USER 10001:10001
WORKDIR /home/shallower
ENV PYTHONUNBUFFERED=1
HEALTHCHECK CMD ["biomero-shallower", "health"]
ENTRYPOINT ["biomero-shallower"]
CMD ["health"]
