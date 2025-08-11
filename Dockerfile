FROM python:3.12-alpine

# Install dependencies
RUN apk update && apk upgrade

RUN python -m pip install --upgrade pip
RUN pip install --no-cache-dir python-binance

WORKDIR /app
COPY . /app

CMD ["python", "main.py"]