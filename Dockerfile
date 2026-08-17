# Modifications Copyright (c) 2026 wslinnn
# This file has been modified from the upstream project
# https://github.com/CHEN010325/paddleocr-local (Apache-2.0).

FROM python:3.10-slim@sha256:a45c323edaa44976ef63b9a85e0d3bd7bbf31676029dccfbc119f88a65311852

WORKDIR /app

# The web container only serves FastAPI, converts Office files to PDF, and
# proxies requests to the official PaddleOCR-VL containers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-writer \
    libreoffice-common \
    default-jre \
    fonts-noto-cjk \
    fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY unlimited_ocr_adapter.py .
COPY ovisocr2_adapter.py .
COPY rapidocr_adapter.py .
COPY Dockerfile.ocr Dockerfile.unlimited-ocr Dockerfile.unlimited-ocr-sglang ./
COPY Dockerfile.ovisocr2 ./
COPY Dockerfile.rapidocr ./
COPY static/ ./static/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["python", "server.py"]
