# rosterd coordinator service. Single instance, federates every site's kernel.
#
# Build context is the REPO ROOT, not this directory -- see
# docker-compose.yml's `build.context`/`dockerfile` for this service. See
# rosterd-kernel/Dockerfile's comment for the full rationale (same pattern).
FROM python:3.12-slim

WORKDIR /app

COPY libs/rosterd-contracts /app/libs/rosterd-contracts

COPY rosterd-coordinator/ /app/rosterd-coordinator/
WORKDIR /app/rosterd-coordinator
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8300
EXPOSE 8300

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8300"]
