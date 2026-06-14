// Auto-staged by Dark Factory (postgres adapter). DO NOT EDIT.
//
// Per-test DB clean-state reset for durable-state (Postgres) specs. Registers a
// vitest `beforeEach` that runs the catalog-driven `_df_reset.sql`
// (TRUNCATE ... RESTART IDENTITY CASCADE) so the id sequence and row set are
// deterministic before every test — making "fresh table" / absolute-id
// assertions valid regardless of how the test-engineer wrote them.
//
// Wired in via the framework-generated vitest wrapper's `setupFiles` (see
// scripts/lib/scenarios.sh) ONLY for postgres-active specs. Driver-adaptive: it
// opens its OWN connection from DATABASE_URL using whichever pg driver the
// implementation already installed (postgres.js or node-postgres `pg`). It never
// imports the project's own client (whose path is a spec free variable).
//
// Driver RESOLUTION: this file lives under .dark-factory/scenarios/, not inside
// the project/workspace package, so a bare import would resolve from the wrong
// node_modules in a monorepo. The gate sets DF_RESET_RESOLVE_BASE to a file in
// the package that owns the driver (the workspace package.json, or the project
// root); we resolve the driver path from THERE, then import the resolved
// absolute path (handles both ESM `postgres` and CJS `pg`).
import { beforeEach, afterAll } from 'vitest';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const __dir = dirname(fileURLToPath(import.meta.url));
const RESET_SQL = readFileSync(join(__dir, '_df_reset.sql'), 'utf8');

interface ResetRunner {
  run(sql: string): Promise<void>;
  close(): Promise<void>;
}

// Lazily created once and reused across all tests in the file (one connection,
// closed in afterAll) — per-test connect would exhaust the pool over many tests.
let runnerPromise: Promise<ResetRunner> | null = null;

async function loadModule(name: string): Promise<unknown | null> {
  // Resolve from the package that owns the driver (gate-provided base), then
  // import the resolved absolute path so ESM and CJS drivers both load.
  const base = process.env.DF_RESET_RESOLVE_BASE || import.meta.url;
  const requireFromBase = createRequire(base);
  let resolved: string;
  try {
    resolved = requireFromBase.resolve(name);
  } catch {
    return null; // driver not installed in the resolution base
  }
  const mod: unknown = await import(pathToFileURL(resolved).href);
  return (mod as { default?: unknown }).default ?? mod;
}

async function makeRunner(): Promise<ResetRunner> {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error('df-reset: DATABASE_URL is not set; cannot reset DB state');
  }

  // Prefer postgres.js if installed; else node-postgres `pg`.
  const postgresFactory = (await loadModule('postgres')) as
    | ((u: string, opts: unknown) => { unsafe(q: string): Promise<unknown>; end(o: { timeout: number }): Promise<void> })
    | null;
  if (postgresFactory) {
    const sql = postgresFactory(url, { max: 1, onnotice: () => {} });
    return {
      run: async (q) => { await sql.unsafe(q); },
      close: async () => { await sql.end({ timeout: 5 }); },
    };
  }

  const pg = (await loadModule('pg')) as {
    Client: new (cfg: { connectionString: string }) => {
      connect(): Promise<void>;
      query(q: string): Promise<unknown>;
      end(): Promise<void>;
    };
  } | null;
  if (!pg) {
    throw new Error(
      'df-reset: no Postgres driver resolvable (expected `postgres` or `pg` ' +
      'installed in the project). DF_RESET_RESOLVE_BASE=' +
      (process.env.DF_RESET_RESOLVE_BASE || '(unset)'),
    );
  }
  const client = new pg.Client({ connectionString: url });
  await client.connect();
  return {
    run: async (q) => { await client.query(q); },
    close: async () => { await client.end(); },
  };
}

beforeEach(async () => {
  if (!runnerPromise) {
    runnerPromise = makeRunner();
  }
  const runner = await runnerPromise;
  await runner.run(RESET_SQL);
});

afterAll(async () => {
  if (runnerPromise) {
    const runner = await runnerPromise;
    await runner.close();
    runnerPromise = null;
  }
});
