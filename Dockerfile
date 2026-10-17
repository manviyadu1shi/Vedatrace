# Read-only replay deployment of VedaTrace (FalkorDB + seed + recorded run + UI) in one container.
#   docker build -t vedatrace .   &&   docker run -p 7860:7860 vedatrace      ->  http://localhost:7860
FROM falkordb/falkordb:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-deploy.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements-deploy.txt
COPY vedatrace/ /app/vedatrace/
COPY deploy/start.sh /app/start.sh
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh
ENV VEDATRACE_READONLY=1 VEDATRACE_MEMORY=off PYTHONUNBUFFERED=1 PYTHONUTF8=1
EXPOSE 7860
ENTRYPOINT ["/bin/sh", "/app/start.sh"]
