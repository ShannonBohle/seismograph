# One-command run for judges: replays the recorded live run (SAMPLE_MODE) with no keys, Zetaris or Nightshift.
#   docker build -t seismograph . && docker run --rm -p 8000:8000 seismograph
#   curl -X POST localhost:8000/run -H "Content-Type: application/json" -d @samples/02_ask.input.json
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV SAMPLE_MODE=true PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "seismograph", "serve", "--port", "8000"]
