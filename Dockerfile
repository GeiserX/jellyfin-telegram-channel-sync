FROM python:3.14-slim

# The base image's ca-certificates 20250419 still trusts the compromised eMudhra
# intermediates; trixie-security carries the release that distrusts them.
RUN apt-get update && apt-get install -y --only-upgrade ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Environment variable for logging
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install dependencies
COPY app/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application package
COPY app/ app/

# Persist the database and both Telethon session files via bind mount
VOLUME ["/app/data"]

CMD ["python", "-m", "app.main"]
