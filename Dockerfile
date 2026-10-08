FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PORT=8000
# Satu worker saja agar penjadwal tidak berjalan ganda
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
