# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3100 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 10001 aurora && adduser -S -u 10001 -G aurora aurora
COPY --from=build --chown=aurora:aurora /app/.next/standalone ./
COPY --from=build --chown=aurora:aurora /app/.next/static ./.next/static
USER aurora
EXPOSE 3100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:3100/login || exit 1
# API_URL (server-side only) points the BFF at the API; it is never exposed to the browser.
CMD ["node", "server.js"]
