# Intelli-Credit API / worker image
FROM python:3.12-slim AS base

ARG INSTALL_TESSERACT=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# libgl1/libglib2.0-0: OpenCV runtime for RapidOCR; libgomp1: OpenMP runtime for LightGBM. Tesseract is optional.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
 && if [ "$INSTALL_TESSERACT" = "true" ]; then apt-get install -y --no-install-recommends tesseract-ocr; fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /app/storage /app/data/synthetic \
 && chown -R appuser:appuser /app/storage /app/data \
 && chmod +x /app/docker-entrypoint.sh

USER appuser
ENV STORAGE_DIR=/app/storage SYNTHETIC_DATA_DIR=/app/data/synthetic
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
