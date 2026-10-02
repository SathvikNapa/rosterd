# rosterd ingestion service (see repo-root docker-compose.yml, the
# commented `ingestion` block). No Dockerfile existed for this service
# before -- added as part of the hexagonal restructuring, same pattern as
# rosterd-kernel/rosterd-coordinator/rosterd-demo-agent's Dockerfiles.
#
# Build context is the REPO ROOT, not this directory -- that's the only way
# this image can also see libs/rosterd-contracts, which requirements.txt
# installs as an editable local dependency (`-e ../libs/rosterd-contracts`)
# and which lives outside this folder -- WORKDIR is switched to
# /app/rosterd-ingestion before `pip install -r requirements.txt` runs
# specifically so that relative `../` resolves to /app/libs/rosterd-contracts.
FROM python:3.12-slim

# git is a real runtime dependency, not a build-time one: adapters/filesystem/repo.py
# shells out to `git clone` for every /ingest call.
RUN apt-get update && apt-get install --no-install-recommends -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY libs/rosterd-contracts /app/libs/rosterd-contracts

COPY rosterd-ingestion/ /app/rosterd-ingestion/
WORKDIR /app/rosterd-ingestion
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:%s/healthz' % os.getenv('PORT','8000'))"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
