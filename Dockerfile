FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY app/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy script
COPY app/sync.py sync.py

# Persist DB and Telethon session files via bind mount (handled in docker-compose)
CMD ["python", "sync.py"]