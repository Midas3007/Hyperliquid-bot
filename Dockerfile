FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hlbot ./hlbot
COPY main.py protocol.toml calendar.toml ./
CMD ["python", "main.py"]
