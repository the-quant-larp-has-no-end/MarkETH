# syntax=docker/dockerfile:1

# MarkETH web image: the Next.js app (SPEC §9, §11).
#
#   prod  `next build` with output: 'standalone', then only the standalone server
#         (the default target)
#   dev   every dependency and `next dev`; compose.override.yaml mounts the source over
#         /app and keeps node_modules and .next in volumes
#
# Everything runs as the image's unprivileged `node` user. The app reads no environment
# variables (A.11), so the build needs none either.

FROM node:24-alpine AS base
# Next.js and its native dependencies ship musl builds; this covers a package that only
# ships a glibc one.
RUN apk add --no-cache libc6-compat
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
RUN chown node:node /app
USER node
EXPOSE 3000

# Dependencies come from the lockfile alone, so this layer is rebuilt only when it changes.
FROM base AS deps
COPY --chown=node:node package.json package-lock.json ./
RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 \
    npm ci

FROM deps AS dev
ENV NODE_ENV=development
# In the dev stack node_modules is a volume, so it outlives the image it was first
# filled from. The entrypoint reinstalls when the mounted package-lock.json is no longer
# the one node_modules was installed from. .next is created here so that its volume
# starts out owned by `node`.
RUN cp package-lock.json node_modules/.installed-lock \
    && mkdir .next
COPY --chmod=755 <<'EOF' /usr/local/bin/dev-entrypoint
#!/bin/sh
set -e
if ! cmp -s package-lock.json node_modules/.installed-lock; then
    npm ci
    cp package-lock.json node_modules/.installed-lock
fi
exec "$@"
EOF
COPY --chown=node:node . .
ENTRYPOINT ["dev-entrypoint"]
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]

FROM deps AS build
COPY --chown=node:node . .
# The final stage copies public/, which an App Router project may not have.
RUN mkdir -p public \
    && npm run build

FROM base AS prod
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
CMD ["node", "server.js"]
