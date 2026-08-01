# syntax=docker/dockerfile:1

FROM node:22-slim AS deps
WORKDIR /srv
COPY package.json package-lock.json ./
# Skips the browser download @playwright/test's postinstall would otherwise
# pull. The package is still installed; only the binaries are skipped.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
# Not --omit=optional. Next's SWC compiler, Tailwind v4's oxide engine and
# lightningcss all ship as platform-specific *optional* dependencies — verified
# against package-lock.json — so omitting them removes the three things the
# build needs most.
RUN npm ci

FROM node:22-slim AS build
WORKDIR /srv
ENV NEXT_TELEMETRY_DISABLED=1
# Switches next.config.ts to standalone output. Set here rather than in the
# config unconditionally, because `next start` refuses to run against it and
# that is what local development and the Playwright suite use.
ENV NEXT_BUILD_STANDALONE=1
COPY --from=deps /srv/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim AS run
WORKDIR /srv
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

RUN useradd --create-home --uid 10002 jerry

# Standalone emits its own traced node_modules and a server.js, so nothing from
# the build toolchain — Playwright, Vitest, TypeScript, Tailwind — reaches the
# runtime image. Static assets and public/ are not traced and come separately.
COPY --from=build --chown=jerry:jerry /srv/.next/standalone ./
COPY --from=build --chown=jerry:jerry /srv/.next/static ./.next/static
COPY --from=build --chown=jerry:jerry /srv/public ./public

USER jerry

# The host assigns the port. Render sets PORT and fails the deploy when nothing
# is listening on it; the standalone server reads both of these natively, and
# HOSTNAME must be 0.0.0.0 or the health check never reaches inside.
ENV PORT=3000 HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["node", "server.js"]
