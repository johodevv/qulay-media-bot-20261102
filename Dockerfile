FROM debian:bookworm-slim AS bot-api-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git gperf libssl-dev zlib1g-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Official source and its pinned TDLib submodule. No third-party Bot API binary.
RUN git init /src \
    && git -C /src remote add origin https://github.com/tdlib/telegram-bot-api.git \
    && git -C /src fetch --depth 1 origin e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1 \
    && git -C /src checkout FETCH_HEAD \
    && git -C /src submodule update --init --recursive --depth 1 \
    && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/out \
    && cmake --build /build --target install -j2

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg tesseract-ocr tesseract-ocr-uzb tesseract-ocr-uzb-cyrl \
    tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY --from=bot-api-builder /out/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY botapp ./botapp
RUN useradd --create-home appuser
USER appuser
EXPOSE 8080
CMD ["python", "-m", "botapp"]
