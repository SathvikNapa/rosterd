# Build the SPA, then serve it as static files.
#
# Vite bakes VITE_* in at BUILD time, and the browser runs on the host, not in
# the compose network — so these default to host-visible URLs (localhost:...),
# not service names like http://kernel-site-a:8100, which a browser could not
# resolve. Override per build with --build-arg if the stack is somewhere else.
FROM node:22-alpine AS build

ARG VITE_INGESTION_URL=http://localhost:8000
ARG VITE_KERNEL_URL=http://localhost:8100
ARG VITE_COORDINATOR_URL=http://localhost:8300
ARG VITE_SPACETIMEDB_URL=http://localhost:3000
ARG VITE_SPACETIMEDB_MODULE=rosterd
ARG VITE_JAEGER_BASE_URL=http://localhost:16686
ARG VITE_SITE_ID=site-a
ARG VITE_ROSTERD_MODE=live

ENV VITE_INGESTION_URL=$VITE_INGESTION_URL \
    VITE_KERNEL_URL=$VITE_KERNEL_URL \
    VITE_COORDINATOR_URL=$VITE_COORDINATOR_URL \
    VITE_SPACETIMEDB_URL=$VITE_SPACETIMEDB_URL \
    VITE_SPACETIMEDB_MODULE=$VITE_SPACETIMEDB_MODULE \
    VITE_JAEGER_BASE_URL=$VITE_JAEGER_BASE_URL \
    VITE_SITE_ID=$VITE_SITE_ID \
    VITE_ROSTERD_MODE=$VITE_ROSTERD_MODE

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 5173
