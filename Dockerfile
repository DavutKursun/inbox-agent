# inbox-agent: API + review UI (Milestone 4). Runs as a non-root user on port 7860, as Hugging Face Docker Spaces expect.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd -m -u 1000 user && mkdir -p /app && chown user:user /app  # the app dir must be writable (budget file, fixtures)
WORKDIR /app

COPY --chown=user requirements-core.txt ./
RUN pip install --no-cache-dir -r requirements-core.txt

# the fake CRM is rebuilt at start-up when data/crm.sqlite is missing; data/ stays writable for the app user
COPY --chown=user . .
USER user

EXPOSE 7860
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
