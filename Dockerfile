# SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GECAA_HOME=/app
WORKDIR /app

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY blueprints ./blueprints
COPY data ./data
COPY schema ./schema

RUN useradd --create-home gecaa && mkdir -p /app/data/knowledge/snapshots && chown -R gecaa /app/data/knowledge
USER gecaa

EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "gecaa.api:app", "--host", "0.0.0.0", "--port", "8000"]
