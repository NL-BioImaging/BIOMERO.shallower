# Build from this repository; schema contracts come from the published package.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.dev0
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS wheels
ARG SETUPTOOLS_SCM_PRETEND_VERSION
WORKDIR /build
RUN pip install --no-cache-dir setuptools==80.9.0 setuptools-scm==10.2.3 wheel==0.45.1 packaging==26.3
COPY pyproject.toml README.md LICENSE /build/shallower/
COPY biomero_shallower /build/shallower/biomero_shallower
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BIOMERO_SHALLOWER=$SETUPTOOLS_SCM_PRETEND_VERSION \
    pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /build/shallower

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
ARG SETUPTOOLS_SCM_PRETEND_VERSION
LABEL org.opencontainers.image.title="BIOMERO.shallower" \
      org.opencontainers.image.source="https://github.com/NL-BioImaging/BIOMERO.shallower" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${SETUPTOOLS_SCM_PRETEND_VERSION}" \
      org.biomeroproject.shallower.capability-schema="1" \
      org.biomeroproject.shallower.runtime-contracts="1" \
      org.biomeroproject.shallower.manifest-schemas="2" \
      org.biomeroproject.shallower.migrations="canonical-single-store,schema-1-to-2,schema-1-path-only-labels"
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
