# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS node-toolchain

FROM python:3.12-slim-bookworm AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 \
    PATH=/app/.venv/bin:$PATH

COPY --from=node-toolchain /usr/local/bin/node /usr/local/bin/node
COPY --from=node-toolchain /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

FROM base AS development-dependencies
COPY package.json package-lock.json ./
RUN npm ci

FROM base AS production-dependencies
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

FROM base AS python-dependencies
COPY requirements.txt ./
RUN python -m venv /app/.venv \
    && pip install --no-cache-dir --requirement requirements.txt

FROM base AS builder
ENV NODE_ENV=production
COPY --from=development-dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run validate:frontend-data && npm run build

FROM base AS runner
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000

RUN groupadd --system --gid 1001 nodejs \
    && useradd --system --uid 1001 --gid nodejs nextjs \
    && mkdir -p /app/runtime \
    && chown nextjs:nodejs /app/runtime

COPY --from=python-dependencies --chown=nextjs:nodejs /app/.venv ./.venv
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=production-dependencies --chown=nextjs:nodejs /app/node_modules ./node_modules
COPY --from=builder --chown=nextjs:nodejs /app/data ./data
COPY --from=builder --chown=nextjs:nodejs /app/scripts ./scripts
COPY --from=builder --chown=nextjs:nodejs /app/src/data ./src/data
COPY --from=builder --chown=nextjs:nodejs /app/src/lib ./src/lib
COPY --from=builder --chown=nextjs:nodejs /app/package.json ./package.json

USER nextjs
EXPOSE 3000
VOLUME ["/app/runtime"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["node", "-e", "fetch(`http://127.0.0.1:${process.env.PORT || 3000}/api/health`).then((response) => { if (!response.ok) process.exit(1) }).catch(() => process.exit(1))"]

CMD ["node", "scripts/start_dynamic.mjs"]
