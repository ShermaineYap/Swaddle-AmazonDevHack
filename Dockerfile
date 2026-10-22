FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp>=2.3,<3" "numpy>=1.26" "uvicorn>=0.30" "starlette>=0.37"
COPY swaddle ./swaddle
COPY web ./web
ENV SWADDLE_PUBLIC=1 PORT=8000 SWADDLE_DB=/tmp/swaddle.db
EXPOSE 8000
CMD ["python", "-m", "swaddle.server"]
