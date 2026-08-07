# Loudrr web (Next.js 16) — Coolify deploy.
#
# NOTE: NEXT_PUBLIC_* vars are INLINED INTO THE CLIENT BUNDLE AT BUILD TIME, not read at
# runtime. So NEXT_PUBLIC_LOUDRR_API must arrive as a build ARG (Coolify injects build-time
# env vars as ARGs). Setting it only as a runtime env var silently ships a build with no API
# configured, and the whole app falls back to mock data.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_LOUDRR_API=""
ENV NEXT_PUBLIC_LOUDRR_API=$NEXT_PUBLIC_LOUDRR_API
ENV NEXT_TELEMETRY_DISABLED=1
# webpack, not turbopack: turbopack OOMs on constrained builders
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

# standalone output: server.js + minimal traced node_modules
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
