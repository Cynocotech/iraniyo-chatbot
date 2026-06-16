FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for WeasyPrint (PDF generation)
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    libglib2.0-0 \
    shared-mime-info \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# 🔒 Security: Create non-root user
RUN groupadd -r -g 1000 appuser && \
    useradd -r -u 1000 -g appuser -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app

# Install Python dependencies as root
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chown=appuser:appuser . .

# 🔒 Security: Switch to non-root user
USER appuser

# Enable unbuffered Python output for better logging
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
