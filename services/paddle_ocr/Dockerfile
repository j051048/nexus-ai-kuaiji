FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_PDX_MODEL_SOURCE=BOS \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_CACHE_HOME=/models \
    OMP_NUM_THREADS=2 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 ocr \
    && mkdir -p /models /app/services/paddle_ocr \
    && chown -R ocr:ocr /models /app

WORKDIR /app
COPY requirements.lock /app/services/paddle_ocr/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r services/paddle_ocr/requirements.lock
COPY --chown=ocr:ocr . /app/services/paddle_ocr/
USER ocr

EXPOSE 8080
HEALTHCHECK --start-period=600s --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','8080')+'/healthz', timeout=3)" || exit 1
# One model instance; concurrency is bounded by PrivateBoundary, not an in-memory queue.
CMD ["sh", "-c", "exec uvicorn services.paddle_ocr.app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --limit-concurrency 8 --timeout-keep-alive 5 --no-access-log"]
