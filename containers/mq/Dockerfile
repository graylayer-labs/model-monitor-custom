FROM 000000000000.dkr.ecr.eu-west-1.amazonaws.com/mmc/analyser-base:sha-placeholder
COPY pyproject.toml src/ ./
RUN uv pip install --system .
ENV MMC_ANALYSER_MODULE=analyser_mq.analyser:MqAnalyser
