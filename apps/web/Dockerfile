# Production image for the web app: a static build, served by nginx.
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
RUN pnpm --filter @nexovue/web build

FROM nginx:1.29-alpine
# The app is a single-page router, so every unknown path serves index.html and
# TanStack Router decides what it is — including the 404.
COPY deploy/nginx/web.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
EXPOSE 8080
