FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/nexus_backend \
    ACCOUNTING_ENV=production \
    ACCOUNTING_AUTH_MODE=supabase \
    ACCOUNTING_SEED=0 \
    ACCOUNTING_QUEUE_EMBEDDED_WORKER=0 \
    ACCOUNTING_SERVICE_ROLE=api \
    PORT=8080

WORKDIR /app
COPY nexus_backend/requirements.txt /app/nexus_backend/requirements.txt
COPY nexus_backend/requirements.runtime.lock /app/nexus_backend/requirements.runtime.lock
RUN pip install --no-cache-dir -r /app/nexus_backend/requirements.runtime.lock \
    && groupadd --gid 10001 accounting \
    && useradd --uid 10001 --gid accounting --no-create-home accounting

COPY nexus_backend/app /app/nexus_backend/app
COPY nexus_backend/migrations /app/nexus_backend/migrations
COPY scripts /app/scripts

USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "scripts/run_accounting_service.py"]
