# Lightweight image for running the reconciliation agent on a schedule
# (cron, Kubernetes CronJob, ECS scheduled task, etc).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Runtime state (checksums, reports) should be on a mounted volume in
# production so change-detection and history persist across container
# restarts - see README "Deployment" section.
VOLUME ["/app/state"]

ENTRYPOINT ["python", "run.py"]
CMD ["--once"]
