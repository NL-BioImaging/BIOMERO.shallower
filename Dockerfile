ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.dev0
FROM python:3.12.16-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS base
# Bootstrap pip's bundled certificate store before either stage downloads packages.
# https://discuss.python.org/t/109576
RUN python -m pip install --no-cache-dir 'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'

FROM base AS wheels
ARG SETUPTOOLS_SCM_PRETEND_VERSION
WORKDIR /build
RUN pip install --no-cache-dir setuptools==80.9.0 setuptools-scm==10.2.3 wheel==0.45.1 packaging==26.3
COPY pyproject.toml README.md LICENSE /build/shallower/
COPY biomero_shallower /build/shallower/biomero_shallower
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_BIOMERO_SHALLOWER=$SETUPTOOLS_SCM_PRETEND_VERSION \
    pip wheel --no-deps --no-build-isolation --wheel-dir /wheels /build/shallower

FROM base
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
