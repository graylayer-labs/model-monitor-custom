# mmc/analyser-base — shared analyser base image.
# See docs/design/002-container-base.md.

FROM python:3.12-slim

ARG IMAGE_DIGEST=local-build

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_SYSTEM_PYTHON=1

RUN pip install --no-cache-dir uv==0.5.*

WORKDIR /opt/mmc

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN uv pip install --system .

RUN echo "${IMAGE_DIGEST}" > /opt/mmc/image_digest

ENTRYPOINT ["python", "-m", "mmc_base.entrypoint"]
