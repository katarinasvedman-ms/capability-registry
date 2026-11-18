FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml requirements-maf.lock ./
COPY adapters adapters
COPY selection selection
COPY tools tools
COPY mocks mocks
COPY governance governance
COPY gateway gateway
COPY consumer consumer
COPY catalog catalog
COPY evaluation/settings.json evaluation/settings.json
COPY evaluation/settings-nano.json evaluation/settings-nano.json
RUN pip install --no-cache-dir -c requirements-maf.lock '.[maf,governance]' \
    && useradd --uid 10001 --create-home app
USER 10001
EXPOSE 8088
CMD ["python", "-m", "tools.serve_governance"]
