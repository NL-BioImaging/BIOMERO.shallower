# Build with D:\workspace as context; no running deployment is required.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS wheels
ARG SCHEMA_VERSION=0.2.1.dev1
ENV SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BIOMERO_SCHEMA=${SCHEMA_VERSION}
WORKDIR /build
RUN pip install --no-cache-dir setuptools==80.9.0 setuptools-scm==9.2.0 wheel==0.45.1 packaging==26.3
COPY biomero-schema/pyproject.toml biomero-schema/README.md /build/schema/
COPY biomero-schema/src /build/schema/src
COPY biomero-shallower/pyproject.toml /build/shallower/
COPY biomero-shallower/biomero_shallower /build/shallower/biomero_shallower
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /build/schema /build/shallower

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
LABEL org.opencontainers.image.title="BIOMERO.shallower" \
      org.opencontainers.image.version="0.1.0"
COPY biomero-shallower/requirements.lock /tmp/requirements.lock
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
