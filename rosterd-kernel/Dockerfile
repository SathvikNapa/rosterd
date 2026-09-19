# rosterd kernel service. One instance per site (see repo-root docker-compose.yml).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8100
EXPOSE 8100

# ROSTERD_KERNEL_DOCKER_MODE=real needs the host's Docker socket mounted
# (-v /var/run/docker.sock:/var/run/docker.sock) so this container can
# start/kill sibling demo-agent containers via the Docker SDK. See README
# "Running against real Docker".
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
