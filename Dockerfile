FROM python:3.14-slim

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
