# ADF Pipeline Accelerator - Frontend

A metadata-driven control plane for Azure Data Factory (ADF) that enables users to configure reusable project metadata, choose metadata-driven pipeline templates, generate ADF artifacts, validate them, and push selected artifacts to Git feature branches.

## Tech Stack

- **Framework:** Next.js 15 (App Router)
- **Language:** TypeScript
- **Styling:** Tailwind CSS
- **UI Components:** Custom shadcn/ui-style components
- **State Management:** React hooks with custom data fetching
- **API Client:** Typed fetch wrapper with error handling

## Getting Started

### Prerequisites

- Node.js 22+ (the Docker images use `node:22-alpine`; CI currently runs 20)
- npm

### Installation

```bash
npm install
```

### Development

```bash
npm run dev
```

The application will be available at `http://localhost:3000`.

### Build

```bash
npm run build
```

### Start Production Server

```bash
npm run start
```

### Lint

```bash
npm run lint
```

### Type Check

```bash
npm run typecheck
```

### Tests

```bash
npm run test       # Vitest + jsdom: lib/**/*.test.ts and colocated *.test.tsx
npm run test:e2e   # Playwright, e2e/*.spec.ts (PW_CHANNEL=chrome|msedge to use an installed browser)
```

Most Playwright specs stub `/api/v1/**` in the browser (`e2e/support/api-stub.ts`). The healthcare specs
also call the real worker and backend through `e2e/support/worker_bridge.py`/`backend_bridge.py`, so they
need Python with `backend/` and `worker/` requirements installed (`PYTHON` overrides the interpreter).

## Environment Variables

There is no frontend-specific env file. Every service, including this one, reads the repo's single
parameter file `config/platform.env` (copy `config/platform.env.example`); `next.config.ts` loads it for
`npm run dev`/`npm run build`, and a real environment variable always wins. The two keys the frontend uses:

| Variable | Description | Default |
|----------|-------------|---------|
| `BACKEND_INTERNAL_URL` | Where the same-origin `/api` proxy (`middleware.ts`) forwards, read per request | `http://localhost:8000` |
| `NEXT_PUBLIC_API_BASE_URL` | Direct API origin baked into the bundle; leave empty to use the proxy | empty |

## Project Structure

```
frontend/
├── app/                    # Next.js App Router routes; globals.css holds the design tokens
├── components/
│   ├── shell/              # App shell: sidebar, topbar, navigation.ts, banners, user guide dialog
│   ├── common/             # page-header, empty-state, status-pill, lifecycle-stepper
│   ├── git/  validation/   # Git push dialog, ValidationPanel
│   ├── rules/              # Transformation / DQ rule editors (Build + Onboarding)
│   └── ui/                 # shadcn-style primitives
├── features/               # One folder per nav area: home, teams, projects, pipelines, build,
│                           # drafts, git, catalog, onboarding, templates, artifacts,
│                           # administration, samples, auth
├── lib/
│   ├── api/                # client.ts (all HTTP), hooks.tsx (useResource), empty-values.ts
│   ├── adf/                # Generator reference implementation: spec, pipeline-codegen,
│   │                       # connections, medallion, naming, qualifier, onboarding, sttm,
│   │                       # sttm-data-modular, compliance
│   └── format.ts state.ts utils.ts ...
├── types/                  # Domain types split by area, re-exported from index.ts
├── e2e/                    # Playwright specs + support/ (API stub, Python bridges)
└── public/                 # sample guides, STTM template download
```

The Build wizard is `features/build/create-pipeline-wizard.tsx` (a thin shell); state lives in
`use-pipeline-wizard.ts` and each step in `wizard-*-step.tsx`. `lib/adf/` must stay in parity with
`worker/app/generator/` — see `docs/DEVELOPER_GUIDE.md`.

## Design System

The application uses a custom design system defined in `app/globals.css` with:

- **Colors:** CSS custom properties such as `--bg`, `--surface`, `--ink`, `--muted`, `--line`, the
  brand accents `--iris`/`--forge`, and status colours (`--green`, `--amber`, `--red`, `--blue`, ...)
- **Typography:** Manrope (sans), DM Mono (mono), self-hosted via `next/font`
- **Animations:** fade-in transition

## Key Features

1. **Project Settings:** project metadata, ADF/template/Git defaults, managed identities, integrations
2. **Onboarding:** Bronze object registry (CSV/Excel/manual) and Silver/Gold STTM registry (including the
   Data Modular template)
3. **Build:** one wizard for every pipeline pattern and Bronze/Silver/Gold layer, with DQ rules and
   transformations
4. **Drafts, Artifacts and Catalog:** generate, validate, review artifacts, browse the target Git repo
5. **Git:** push selected artifacts to a feature branch and open a PR into `dev`
6. **Standards and Administration:** naming/tag policies and compliance scan, users, sessions, SSO
   configuration, audit log

See `docs/USER_GUIDE.md` for the screen-by-screen guide.

## API Integration

The frontend communicates with the backend API at `/api/v1/*`. When the backend is unavailable, each screen shows a real loading/error/empty state instead of fabricated sample data.

## Contributing

1. Follow the existing code style and conventions
2. Use TypeScript for all new code
3. Add appropriate type definitions
4. Test changes both with and without a running backend
