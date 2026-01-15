FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY magma_tts_api.py .
EXPOSE 5000
CMD ["python", "magma_tts_api.py"]
