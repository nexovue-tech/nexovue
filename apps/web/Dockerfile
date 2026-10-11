# The console's built files, and nothing that serves them.
#
# The deployment's edge serves them (deploy/caddy/Caddyfile); `web-assets-init`
# copies them out of this image into the edge's volume at deploy time, which is
# why the final stage keeps a shell and `cp`. ADR-019 moves these files into the
# `nexovue` image (group 8 of the readiness plan).
FROM node:26.7.0-trixie-slim AS build
RUN npm install --global corepack@latest && corepack enable
WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/ui/package.json packages/ui/
RUN --mount=type=cache,target=/pnpm-store \
    pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile

COPY packages/ui packages/ui
COPY apps/web apps/web
# The setup pages are documentation first; the build turns them into console
# routes (apps/web/build/setup-docs-plugin.ts).
COPY docs/product/setup docs/product/setup
RUN pnpm --filter @nexovue/web build

FROM busybox:1.37.0
COPY --from=build /app/apps/web/dist /srv/web
