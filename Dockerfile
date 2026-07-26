FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S bot && adduser -S -G bot bot
WORKDIR /app

COPY app ./app
COPY main.py ./

USER bot
ENTRYPOINT ["python", "main.py"]
CMD ["--listen"]
