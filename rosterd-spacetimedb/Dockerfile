# Publishes the rosterd module into the `spacetimedb` compose service, then
# exits. Not a long-running server -- the real server is the official
# clockworklabs/spacetime image (see docker-compose.yml's `spacetimedb`
# service); this container's only job is to run `spacetime publish` against
# it, which needs Node (to bundle the TypeScript module) and the spacetime
# CLI, neither of which the server image ships.
FROM node:20-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The installer only takes -y (non-interactive) and -h -- no version pin
# flag exists, so this tracks whatever it currently resolves as "latest",
# same as clockworklabs/spacetime:v2.10.1 in docker-compose.yml (both
# happen to be 2.10.1 as of this writing; see spacetime --version locally
# if these ever need to be re-aligned).
RUN curl -sSf https://install.spacetimedb.com | sh -s -- -y
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /module
COPY spacetime.json ./
COPY spacetimedb/package.json spacetimedb/tsconfig.json ./spacetimedb/
RUN cd spacetimedb && npm install
COPY spacetimedb/src ./spacetimedb/src

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
