FROM python:3.11-slim

# Instalar Tesseract OCR y Poppler
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads processed templates

EXPOSE 8000

# ✅ 1 worker, timeout 300s, sin threads extra
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "1", "--timeout", "300", "--graceful-timeout", "300", "--max-requests", "10", "--max-requests-jitter", "5"]
