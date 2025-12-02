FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
# Runtime configuration (overridable via docker run -e ...)
ENV HOST=0.0.0.0 \
    PORT=8000 \
    TZID=America/New_York \
    DR_PROFILE=0 \
    SCAN_DAYS=1095
EXPOSE 8000

CMD ["python", "server.py"]
