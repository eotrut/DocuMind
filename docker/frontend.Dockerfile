# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# DocuMind frontend - Next.js 14 (standalone output)
# ---------------------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci


FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

# NEXT_PUBLIC_* values are inlined at build time, so the API URL the browser
# will use has to be known here (not just at runtime).
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build


FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 10001 nodejs \
    && adduser --system --uid 10001 nextjs

# `output: "standalone"` traces exactly the files the server needs.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
