import { NextRequest, NextResponse } from "next/server";

/**
 * Same-origin API proxy (IMPLEMENTATION_PLAN.md gap B-3).
 *
 * Deliberately Middleware, not `next.config.ts`'s `rewrites()`: Next.js
 * resolves `rewrites()` destinations once into `routes-manifest.json` at
 * `next build` time — an env var read there would be baked into the build
 * output exactly like a `NEXT_PUBLIC_*` var, defeating the point. Middleware
 * runs as real server code on every matching request against the *running*
 * container, so `process.env.BACKEND_INTERNAL_URL` here is read fresh per
 * request from whatever environment this container was started in.
 *
 * `BACKEND_INTERNAL_URL` is a plain server-only env var (never
 * `NEXT_PUBLIC_*`), so it is never inlined into the client bundle — the
 * browser only ever calls this app's own origin (`/api/v1/...`); this
 * rewrites that, server-to-server, to wherever this environment's backend
 * actually lives. That's what makes one built image promotable
 * DEV -> QA -> PROD without a rebuild. See `frontend/lib/api/client.ts`'s
 * `API_BASE_URL`, which defaults to "" (relative) for exactly this reason.
 */
export function middleware(request: NextRequest) {
  const backend = (process.env.BACKEND_INTERNAL_URL || "http://localhost:8000").replace(/\/$/, "");
  const target = new URL(request.nextUrl.pathname + request.nextUrl.search, backend);
  return NextResponse.rewrite(target);
}

export const config = {
  matcher: "/api/:path*",
};
