FROM python:3.12-slim

WORKDIR /app

# curl stays in the image: dbmate is fetched with it, and the ECS container
# health check (curl localhost:8080/health) uses it at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 -o /usr/local/bin/dbmate && \
    chmod +x /usr/local/bin/dbmate && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the trimmed servant index from Atlas Academy at build time so the running
# container does no cold-start fetch. (Generated file is gitignored.)
RUN python scripts/sync_atlas.py

# Run migrations, then start the bot. `exec` makes python PID 1 so it receives
# SIGTERM from ECS directly -- needed to release the gateway advisory lock cleanly.
CMD ["sh", "-c", "dbmate -d ./database/migrations up && exec python bot.py"]
