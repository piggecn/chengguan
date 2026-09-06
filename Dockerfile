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

# 单账号工具用 1 个 worker 足够，也避免多 worker 在 import 时并发跑迁移
# （迁移里有 DROP TABLE 和文件 move，两个进程同时跑会打架）。
# timeout 放到 600s：台账导出要把原图整张读进内存，数据量大时确实要几分钟。
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5000", "--timeout", "600", "app:app"]
