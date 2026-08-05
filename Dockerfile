# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py

# Install system dependencies
# - gcc: extensiones C de pip
# - libpango/cairo/etc: WeasyPrint (renderizado de PDF)
# - fonts-dejavu: acentos/tildes y caracteres latinos
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-dejavu \
    fonts-liberation \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy project files
COPY . .

# Usuario no-root + directorios de datos (BD SQLite y adjuntos).
# uid 1000 coincide con el dueño de ./docker-data/quotes en el host.
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data/adjuntos \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5001

# --preload: importa WeasyPrint UNA vez en el proceso maestro y los workers lo
#   heredan por copy-on-write. Sin esto se paga su memoria una vez por worker.
# --workers 3: el host tiene 1 CPU compartida por varios contenedores; con
#   workers sync, más procesos no dan más throughput, solo más RSS.
# --max-requests: WeasyPrint fragmenta memoria; reciclar workers acota el
#   crecimiento en un servidor sin swap.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", \
     "--workers", "3", "--preload", \
     "--max-requests", "300", "--max-requests-jitter", "50", \
     "--timeout", "120", "app:app"]
