# rosterd demo agent (e-commerce). One instance per site (see repo-root
# docker-compose.yml, the commented `demo-agent-site-a` block).
#
# Build context is the REPO ROOT, not this directory -- same reasoning as
# rosterd-kernel/Dockerfile and rosterd-coordinator/Dockerfile: that's the
# only way this image can also see libs/rosterd-contracts, which
# requirements.txt installs as an editable local dependency
# (`-e ../libs/rosterd-contracts`) and which lives outside this folder --
# WORKDIR is switched to /app/rosterd-demo-agent before `pip install -r
# requirements.txt` runs specifically so that relative `../` resolves to
# /app/libs/rosterd-contracts.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Shared contracts package's own files first -- changes far less often
# than service code, so this layer stays cached across most rebuilds.
COPY libs/rosterd-contracts /app/libs/rosterd-contracts

COPY rosterd-demo-agent/ /app/rosterd-demo-agent/
WORKDIR /app/rosterd-demo-agent
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8000 AGENT_MODE=scripted
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://localhost:%s/health' % os.getenv('PORT','8000'))"

# ONE worker on purpose: a paused interrupt() lives in this process's memory.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
