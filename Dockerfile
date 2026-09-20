# Shift-H: clinician leave and cover assistant (WA Health hackathon)
# docker build -t shift-h .   (Cloud Run and Hugging Face Spaces build this automatically)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# listen on all interfaces on the host's port; keep the request database on local disk
ENV HOST=0.0.0.0 PORT=8080 LEAVECOVER_DB=/tmp/shift-h/leavecover.db
# secrets are injected by the host, never baked in: GEMINI_API_KEY, ADMIN_PASSWORD, MANAGER_PASSWORD, STAFF_PASSWORD
RUN useradd -m -u 1000 app && mkdir -p /tmp/shift-h && chown -R app /app /tmp/shift-h   # uid 1000 as Hugging Face Spaces expects
USER app
EXPOSE 8080
CMD ["python", "run.py"]
