# Development image for the web app: Node plus pnpm, nothing else. The source
# tree and node_modules are bind-mounted and the Vite dev server does the rest.
FROM node:26.7.0-trixie-slim

# Node 25 dropped the bundled corepack, so it installs standalone. Corepack
# rather than `npm i -g pnpm` because the version then stays in the root
# package.json `packageManager` field, in one place, for everyone.
RUN npm install --global corepack@latest && corepack enable

WORKDIR /workspace
