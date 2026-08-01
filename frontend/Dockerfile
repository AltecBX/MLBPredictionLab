# syntax=docker/dockerfile:1
FROM node:22-slim AS deps
WORKDIR /srv
COPY package.json package-lock.json ./
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --omit=optional

FROM node:22-slim AS build
WORKDIR /srv
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim AS run
WORKDIR /srv
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /srv/.next ./.next
COPY --from=build /srv/node_modules ./node_modules
COPY --from=build /srv/package.json ./package.json
COPY --from=build /srv/public ./public
COPY --from=build /srv/next.config.ts ./next.config.ts

RUN useradd --create-home --uid 10002 jerry && chown -R jerry /srv
USER jerry

EXPOSE 3000
CMD ["npm", "run", "start"]
