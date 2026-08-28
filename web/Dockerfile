# The web app as a container. Written 2026-08-28 for the self-hosted stack.
#
# WHY THIS EXISTS. `infra/selfhost/docker-compose.yml` needs a web service, and Lanes I
# and J shipped the app without an image — reasonably, since neither owned `infra/`. The
# compose note said to add the service "when those lanes land, with `depends_on: postgres`
# and the same `127.0.0.1:` port binding". They landed.
#
# NODE 24, MATCHING THE HOST THIS WAS BUILT AND TESTED ON (`node --version` -> v24.19.0)
# and the versions Lane I pinned exactly in `package.json` (Next 16.3.3, React 19.2.8).
# `-slim` rather than `-alpine`: Next 16 with Turbopack pulls native binaries, and musl
# builds of those are a separate matter from the glibc ones every CI runner uses. This
# image is the self-hoster's, not a size competition.
#
# TWO STAGES, AND THE SECOND ONE CARRIES NO SOURCE. A single-stage image would ship
# `node_modules` with every dev dependency — eslint, vitest, typescript — into a
# long-running container, which is more attack surface for a build tool nobody runs there.
FROM node:24-slim AS builder

WORKDIR /app

# THE LOCKFILE FIRST, AND `npm ci` NOT `npm install`. `ci` refuses when
# `package-lock.json` disagrees with `package.json`, which is the whole point: an install
# that silently resolved a different tree would build an image nobody's tests ran against.
# Lane I pinned every version exactly for the same reason.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# `next build` REFUSES WITHOUT ALL FOUR OF THESE, and that is Lane I's fail-closed design
# firing at build time rather than a nuisance:
#
#     DATABASE_URL is not set, so there is nowhere to keep sessions. Refused rather than
#     falling back: an in-memory store answers every session lookup with 'nobody is
#     signed in', which is indistinguishable from a working sign-in nobody has used.
#
# So the build needs values, and BUILD-TIME VALUES MUST NOT BE THE RUNTIME ONES. These are
# obvious placeholders that could not authenticate anything; compose supplies the real
# environment at `up`. A real secret baked here would sit in an image layer forever, which
# is the same shape as the `github_pat_` this project already leaked into a Terraform plan
# artifact.
ENV DATABASE_URL="postgresql://build-time-placeholder@127.0.0.1:5432/none" \
    AUTH_SECRET="build-time-placeholder-not-a-credential" \
    AUTH_GITHUB_ID="build-time-placeholder" \
    AUTH_GITHUB_SECRET="build-time-placeholder" \
    NEXT_TELEMETRY_DISABLED="1"

RUN npm run build

# ── the runtime ───────────────────────────────────────────────────────────────
FROM node:24-slim

WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

# PRODUCTION DEPENDENCIES ONLY. `--omit=dev` drops eslint, vitest and typescript, which
# the running app never imports.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/app ./app
COPY --from=builder /app/lib ./lib
COPY --from=builder /app/components ./components
COPY --from=builder /app/next.config.mjs ./

# ── PYTHON, AND WHY A NODE IMAGE NEEDS IT ─────────────────────────────────────
#
# `web/lib/pipeline.ts` spawns one short-lived `python` per read, running a named module
# under `web/lib/reader/`. That is a deliberate architecture, not an accident: a Node
# database client has no `current_tenant()`, so scoped WRITES fail loudly while scoped
# READS SUCCEED UNSCOPED — which is how a tenant leak ships green. The Python readers go
# through `agentorg.tenancy`'s accessors, where the scoping lives.
#
# MEASURED IN THIS CONTAINER before Python was here: `/api/runs` answered
#
#     500 {"error":"that request could not be processed",
#          "detail":"PipelineError: the pipeline reader could not be started"}
#
# while `/api/session` answered 200 — because the session comes from Postgres through the
# Node adapter and never touches a reader. So the failure was invisible on the one route
# somebody checks first, which is the shape this repository is most careful about.
#
# `AGENTORG_PYTHON` is the override seam Lane I already left for exactly this
# (`pipeline.ts:93` falls back to `.venv-main/bin/python`, which no image has). It is set
# in the runtime environment below rather than guessed at.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/*

# The package and its runtime dependencies. `--break-system-packages` because this is a
# single-purpose container with no other Python consumer — the warning is about a shared
# system interpreter, and there is not one here.
# INSTALLED FROM PyPI-STYLE LOCAL PATHS IS NOT POSSIBLE HERE: the build context is
# `web/`, so `agentorg/` was never in it — a `COPY ../agentorg` cannot escape the context
# and a `COPY --from=builder /app/../agentorg` copies nothing, silently. So the compose
# service mounts the repository read-only instead and this image installs only the runtime
# dependencies the readers need.
#
# Deliberately NOT widening the build context to the repository root: that would trace
# `runs/` (~10k gitignored files) into a Node image, which is the same defect
# `pipeline.ts` already records Turbopack committing for a different reason.
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        'pydantic>=2,<3' 'psycopg[binary]>=3,<4' \
    && python3 -c "import pydantic, psycopg; print('reader deps present')"

ENV AGENTORG_PYTHON=/usr/bin/python3

USER node

# NOT ROOT — set above, after the Python install that needs root. `node` is uid 1000 in
# this base image and owns nothing here it needs to write: `.next` is read at runtime, and
# the only state this app has lives in Postgres.

# `HOSTNAME=0.0.0.0` is set by compose, not here. Inside a namespace the server has to
# bind every interface or the host's port publish reaches nothing — and compose publishes
# on `127.0.0.1` only, which is what actually keeps this off the network. Baking the wide
# bind into the image would make `podman run` expose it by default; the same argument
# `agentorg/api/server.py:main` makes about `API_HOST`.
EXPOSE 3000

CMD ["npm", "run", "start"]
