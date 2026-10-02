# rosterd kernel service. One instance per site (see repo-root docker-compose.yml).
#
# Build context is the REPO ROOT, not this directory -- see
# docker-compose.yml's `build.context`/`dockerfile` for this service. That's
# the only way this image can also see libs/rosterd-contracts, which
# requirements.txt installs as an editable local dependency
# (`-e ../libs/rosterd-contracts`) and which lives outside this folder --
# WORKDIR is switched to /app/rosterd-kernel before `pip install -r
# requirements.txt` runs specifically so that relative `../` resolves to
# /app/libs/rosterd-contracts, the same layout requirements.txt already
# assumes for local (non-Docker) development.
FROM python:3.12-slim

WORKDIR /app

# Shared contracts package's own files first -- changes far less often
# than service code, so this layer stays cached across most rebuilds.
COPY libs/rosterd-contracts /app/libs/rosterd-contracts

COPY rosterd-kernel/ /app/rosterd-kernel/
WORKDIR /app/rosterd-kernel
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8100
EXPOSE 8100

# ROSTERD_KERNEL_DOCKER_MODE=real needs the host's Docker socket mounted
# (-v /var/run/docker.sock:/var/run/docker.sock) so this container can
# start/kill sibling demo-agent containers via the Docker SDK. See README
# "Running against real Docker".
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
