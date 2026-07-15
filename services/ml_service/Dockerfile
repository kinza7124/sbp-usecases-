FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn pydantic scikit-learn joblib numpy pandas

COPY ml_service.py .
COPY cutoff.json .
COPY exposure_model.joblib .

EXPOSE 5001

CMD ["uvicorn", "ml_service:app", "--host", "0.0.0.0", "--port", "5001"]
