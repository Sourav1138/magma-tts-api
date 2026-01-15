echo "FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/audio

ENV PORT=5000
EXPOSE \$PORT

CMD gunicorn --workers 3 --bind 0.0.0.0:\$PORT app:app" > Dockerfile
