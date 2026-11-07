# Multi-stage build (IMPLEMENTATION_PLAN.md gap B-2).
#
# Targets:
#   dev        - local docker-compose (source bind-mounted over this, hot reload)
#   production - default target: a real `next build --output standalone` runtime,
#                no dev server, no source bind-mount dependency. CI/CD builds this
#                target implicitly (it's the last stage) for anything beyond
#                local `docker compose up`.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

# ---- dev: docker-compose's target (see docker-compose.yml) ----------------
FROM deps AS dev
COPY . .
CMD ["npm", "run", "dev"]

# ---- build: compiles the standalone production server ---------------------
FROM deps AS build
COPY . .
# BACKEND_INTERNAL_URL is read server-side at *request* time by middleware.ts
# (IMPLEMENTATION_PLAN.md gap B-3) — it must not be baked in here.
# NEXT_PUBLIC_* vars, if ever set, WOULD be inlined at this step; none are
# required for the default same-origin-proxy setup.
RUN npm run build

# ---- production: minimal runtime image (default target) -------------------
FROM node:22-alpine AS production
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
