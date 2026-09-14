FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY yanxu ./yanxu
RUN pip install --no-cache-dir '.[server]'

RUN useradd --create-home --uid 10001 yanxu
USER yanxu
EXPOSE 8080
CMD ["yanxu", "serve", "--host", "0.0.0.0", "--port", "8080"]
