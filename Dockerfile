FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    libgomp1 \
    libseccomp2 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN groupadd --gid 10001 taxedo && useradd --uid 10001 --gid 10001 --no-create-home taxedo
RUN mkdir -p /app/data
RUN python -c "from taxedo.tax.knowledge import warm_embedding_model; warm_embedding_model()"

RUN chown -R 10001:10001 /app/data /app/.model_cache
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD ["python", "-m", "taxedo.runtime.healthcheck"]
CMD ["python", "-m", "taxedo"]
