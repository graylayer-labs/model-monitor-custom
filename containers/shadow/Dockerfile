ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY pyproject.toml src/ ./
RUN uv pip install --system .
ENV MMC_ANALYSER_MODULE=analyser_shadow.analyser:ShadowAnalyser
