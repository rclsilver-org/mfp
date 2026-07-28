FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY scanapp ./scanapp

EXPOSE 8080
CMD ["uvicorn", "scanapp.main:app", "--host", "0.0.0.0", "--port", "8080"]
