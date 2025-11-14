# Railway deployment Dockerfile
FROM python:3.11-slim

# Ish katalogini yaratish
WORKDIR /app

# Sistema paketlarini yangilash va ffmpeg o'rnatish
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Barcha fayllarni ko'chirish
COPY . .

# Kerakli papkalarni yaratish
RUN mkdir -p downloads logs backups data

# Botni ishga tushirish
CMD ["python", "main.py"]
