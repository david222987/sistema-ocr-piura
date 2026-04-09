# Imagen base Python
FROM python:3.11-slim

# Instalar Tesseract OCR y Poppler
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-spa \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Directorio de trabajo
WORKDIR /app

# Copiar archivos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Crear carpetas necesarias
RUN mkdir -p uploads processed

# Puerto
EXPOSE 8000

# Arrancar con gunicorn
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "2"]
