FROM python:3.12-slim

WORKDIR /workspace

# Install kubectl and required system packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash make curl ca-certificates \
    && curl -fsSL "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
       -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/workspace \
    PORT=7860

EXPOSE 7860

ENTRYPOINT ["bash", "scripts/container-entrypoint.sh"]
CMD ["python3", "-m", "tron_openenv.server.app"]
