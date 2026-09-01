FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/uploads
ENV DATA_DIR=/app/data \
    UPLOADS_DIR=/app/uploads \
    TZ=Asia/Shanghai
EXPOSE 5000

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "120", "app:app"]
