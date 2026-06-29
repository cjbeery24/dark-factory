The file write is pending your approval. Here is the full test file content and bridge manifest:

---

## `.dark-factory/holdouts/frontend-shell/holdout.test.ts`

```ts
/**
 * Adversarial holdout tests — QA Holdout role.
 * Spec: Frontend Application Shell (B1–B4).
 *
 * Intended path: .dark-factory/holdouts/frontend-shell/holdout.test.ts
 * Masked from the Implementer at container layer.
 *
 * Every test is tagged [adversarial] + [hb:<id>].
 * NO [AC-N] / [FR-N] / [EC-N] tags.
 * Tests probe inputs and conditions the spec does NOT enumerate.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import React from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { render, cleanup } from '@testing-library/react'

import {
  createApiClient,
  RejectionError,
  ConnectivityError,
} from '../../../apps/web/src/lib/apiClient.js'
import { routes } from '../../../apps/web/src/router.js'
import { getConfig } from '../../../apps/web/src/config.js'

// ── helpers ───────────────────────────────────────────────────────────────────

type MockResponse = Pick<Response, 'ok' | 'status' | 'json' | 'text'>

function fakeFetch(status: number, body: unknown): () => Promise<MockResponse> {
  const ok = status >= 200 && status < 300
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

function fakeFetchOkOverride(
  status: number,
  ok: boolean,
  body: unknown,
): () => Promise<MockResponse> {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  })
}

function failingFetch(reason: unknown): () => Promise<never> {
  return vi.fn().mockRejectedValue(reason)
}

function capturingFetch(status: number, body: unknown) {
  const captured: Record<string, string> = {}
  const fn = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    const headers = new Headers(init?.headers as HeadersInit | undefined)
    headers.forEach((v, k) => { captured[k.toLowerCase()] = v })
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    })
  })
  return { fn, captured }
}

function renderRoute(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return render(React.createElement(RouterProvider, { router }))
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// ─────────────────────────────────────────────────────────────────────────────
// B2 — response envelope parsing edge cases
// ─────────────────────────────────────────────────────────────────────────────

describe('apiClient — response envelope edge cases', () => {

  it('[adversarial] [hb:h01] malformed JSON body does not silently succeed', async () => {
    const badFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.reject(new SyntaxError('Unexpected token < in JSON at position 0')),
      text: () => Promise.resolve('<html>502 Bad Gateway</html>'),
    })
    const client = createApiClient({ fetchFn: badFetch, getSession: () => null })
    await expect(client.get('/endpoint')).rejects.toThrow()
  })

  it('[adversarial] [hb:h02] HTTP 200 response carrying an error envelope is surfaced as error', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(200, true, { error: { code: 'FORBIDDEN', message: 'denied' } }),
      getSession: () => null,
    })
    await expect(client.get('/resource')).rejects.toThrow()
  })

  it('[adversarial] [hb:h03] HTTP 5xx with data envelope is not treated as success', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(500, false, { data: { id: 42 } }),
      getSession: () => null,
    })
    await expect(client.get('/resource')).rejects.toThrow()
  })

  it('[adversarial] [hb:h04] 204 No Content does not produce an unhandled JSON parse crash', async () => {
    const emptyFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: () => Promise.reject(new SyntaxError('Unexpected end of JSON input')),
      text: () => Promise.resolve(''),
    })
    const client = createApiClient({ fetchFn: emptyFetch, getSession: () => null })
    let threw: unknown = null
    try {
      await client.get('/no-content')
    } catch (e) {
      threw = e
    }
    if (threw !== null) {
      expect(threw).toBeInstanceOf(Error)
    }
  })

  it('[adversarial] [hb:h05] envelope with both data and error fields — error wins', async () => {
    const client = createApiClient({
      fetchFn: fakeFetch(200, { data: 'payload', error: { code: 'CONFLICT', message: 'conflict' } }),
      getSession: () => null,
    })
    await expect(client.get('/ambiguous')).rejects.toThrow()
  })

  it('[adversarial] [hb:h06] error envelope missing code field still throws RejectionError', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(400, false, { error: { message: 'something went wrong' } }),
      getSession: () => null,
    })
    await expect(client.get('/endpoint')).rejects.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h07] error envelope missing message field still throws RejectionError', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(400, false, { error: { code: 'BAD_REQUEST' } }),
      getSession: () => null,
    })
    await expect(client.get('/endpoint')).rejects.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h08] error code that is a number still surfaces as RejectionError', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(422, false, { error: { code: 422, message: 'unprocessable entity' } }),
      getSession: () => null,
    })
    await expect(client.get('/endpoint')).rejects.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h09] data: null is a valid success payload, not an error', async () => {
    const client = createApiClient({
      fetchFn: fakeFetch(200, { data: null }),
      getSession: () => null,
    })
    await expect(client.get('/nullable')).resolves.not.toThrow()
  })

  it('[adversarial] [hb:h10] completely empty envelope {} does not silently return data', async () => {
    const client = createApiClient({
      fetchFn: fakeFetch(200, {}),
      getSession: () => null,
    })
    let result: unknown
    let threw = false
    try {
      result = await client.get('/empty-envelope')
    } catch {
      threw = true
    }
    if (!threw) {
      expect(result).toBeUndefined()
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// B2 — ConnectivityError vs RejectionError distinction
// ─────────────────────────────────────────────────────────────────────────────

describe('apiClient — ConnectivityError vs RejectionError', () => {

  it('[adversarial] [hb:h11] fetch rejection with a string becomes ConnectivityError', async () => {
    const client = createApiClient({ fetchFn: failingFetch('network is down'), getSession: () => null })
    await expect(client.get('/ep')).rejects.toBeInstanceOf(ConnectivityError)
  })

  it('[adversarial] [hb:h12] fetch rejection with null becomes ConnectivityError', async () => {
    const client = createApiClient({ fetchFn: failingFetch(null), getSession: () => null })
    await expect(client.get('/ep')).rejects.toBeInstanceOf(ConnectivityError)
  })

  it('[adversarial] [hb:h13] fetch rejection with a plain object becomes ConnectivityError', async () => {
    const client = createApiClient({
      fetchFn: failingFetch({ type: 'NetworkError', retryable: true }),
      getSession: () => null,
    })
    await expect(client.get('/ep')).rejects.toBeInstanceOf(ConnectivityError)
  })

  it('[adversarial] [hb:h14] RejectionError and ConnectivityError are mutually exclusive instanceof checks', async () => {
    const rejClient = createApiClient({
      fetchFn: fakeFetchOkOverride(400, false, { error: { code: 'E', message: 'm' } }),
      getSession: () => null,
    })
    const connClient = createApiClient({
      fetchFn: failingFetch(new TypeError('Failed to fetch')),
      getSession: () => null,
    })

    const rejErr = await rejClient.get('/ep').catch(e => e)
    const connErr = await connClient.get('/ep').catch(e => e)

    expect(rejErr).toBeInstanceOf(RejectionError)
    expect(connErr).toBeInstanceOf(ConnectivityError)
    expect(rejErr).not.toBeInstanceOf(ConnectivityError)
    expect(connErr).not.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h15] both error types are instanceof Error', async () => {
    const rejClient = createApiClient({
      fetchFn: fakeFetchOkOverride(403, false, { error: { code: 'E', message: 'm' } }),
      getSession: () => null,
    })
    const connClient = createApiClient({
      fetchFn: failingFetch(new TypeError('Failed to fetch')),
      getSession: () => null,
    })
    const rejErr = await rejClient.get('/ep').catch(e => e)
    const connErr = await connClient.get('/ep').catch(e => e)

    expect(rejErr).toBeInstanceOf(Error)
    expect(connErr).toBeInstanceOf(Error)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// B2 — RejectionError identity preservation
// ─────────────────────────────────────────────────────────────────────────────

describe('RejectionError — backend error identity', () => {

  it('[adversarial] [hb:h16] RejectionError carries the backend HTTP status code', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(403, false, { error: { code: 'FORBIDDEN', message: 'no access' } }),
      getSession: () => null,
    })
    const err = await client.get('/secured').catch(e => e)
    expect(err).toBeInstanceOf(RejectionError)
    expect((err as RejectionError).status).toBe(403)
  })

  it('[adversarial] [hb:h17] RejectionError carries the backend error code string', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(422, false, { error: { code: 'VALIDATION_ERROR', message: 'bad' } }),
      getSession: () => null,
    })
    const err = await client.get('/form').catch(e => e)
    expect(err).toBeInstanceOf(RejectionError)
    expect((err as RejectionError).code).toBe('VALIDATION_ERROR')
  })

  it('[adversarial] [hb:h18] RejectionError message contains the backend error message text', async () => {
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(400, false, { error: { code: 'MISSING', message: 'field x is required' } }),
      getSession: () => null,
    })
    const err = await client.get('/submit').catch(e => e)
    expect(err).toBeInstanceOf(RejectionError)
    expect((err as RejectionError).message).toContain('field x is required')
  })

  it('[adversarial] [hb:h19] oversized error code (10 000 chars) is preserved, not truncated', async () => {
    const longCode = 'E'.repeat(10_000)
    const client = createApiClient({
      fetchFn: fakeFetchOkOverride(400, false, { error: { code: longCode, message: 'big' } }),
      getSession: () => null,
    })
    const err = await client.get('/endpoint').catch(e => e)
    expect(err).toBeInstanceOf(RejectionError)
    expect((err as RejectionError).code).toBe(longCode)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// B3 — authentication edge cases
// ─────────────────────────────────────────────────────────────────────────────

describe('apiClient — authentication edge cases', () => {

  // Differential pair dp01 ────────────────────────────────────────────────────
  it('[adversarial] [hb:h20] valid session token — Authorization header is attached [control]', async () => {
    const { fn, captured } = capturingFetch(200, { data: 'ok' })
    const client = createApiClient({ fetchFn: fn, getSession: () => 'valid-token-abc123' })
    await client.get('/endpoint')
    expect(captured['authorization']).toBeDefined()
    expect(captured['authorization']).toContain('valid-token-abc123')
  })

  it('[adversarial] [hb:h21] null session — no Authorization header is sent [rejection]', async () => {
    const { fn, captured } = capturingFetch(200, { data: 'ok' })
    const client = createApiClient({ fetchFn: fn, getSession: () => null })
    await client.get('/endpoint')
    expect(captured['authorization']).toBeUndefined()
  })
  // ── end dp01 ────────────────────────────────────────────────────────────────

  it('[adversarial] [hb:h22] empty string session is not sent as a credential', async () => {
    const { fn, captured } = capturingFetch(200, { data: 'ok' })
    const client = createApiClient({ fetchFn: fn, getSession: () => '' })
    await client.get('/endpoint').catch(() => {})
    const auth = captured['authorization']
    const hasRealAuth = auth !== undefined && auth.trim() !== ''
    expect(hasRealAuth).toBe(false)
  })

  it('[adversarial] [hb:h23] whitespace-only session token is not fabricated as credential', async () => {
    const { fn, captured } = capturingFetch(200, { data: 'ok' })
    const client = createApiClient({ fetchFn: fn, getSession: () => '   \t  ' })
    await client.get('/endpoint').catch(() => {})
    const auth = captured['authorization']
    const hasNonEmptyAuth = auth !== undefined && auth.replace(/\s/g, '') !== ''
    expect(hasNonEmptyAuth).toBe(false)
  })

  it('[adversarial] [hb:h24] CRLF in session token does not inject extra headers', async () => {
    const { fn, captured } = capturingFetch(200, { data: 'ok' })
    const injectedToken = 'legit-token\r\nX-Injected: evil-value'
    const client = createApiClient({ fetchFn: fn, getSession: () => injectedToken })
    await client.get('/endpoint').catch(() => {})
    expect(captured['x-injected']).toBeUndefined()
  })

  it('[adversarial] [hb:h25] getSession() throwing propagates — not silently swallowed', async () => {
    const client = createApiClient({
      fetchFn: vi.fn(),
      getSession: () => { throw new Error('session store unavailable') },
    })
    await expect(client.get('/endpoint')).rejects.toThrow()
  })

  it('[adversarial] [hb:h26] getSession() returning undefined behaves as no session', async () => {
    const { fn, captured } = capturingFetch(200, { data: 'ok' })
    const client = createApiClient({
      fetchFn: fn,
      getSession: () => undefined as unknown as null,
    })
    await client.get('/endpoint').catch(() => {})
    const auth = captured['authorization']
    const hasRealAuth = auth !== undefined && auth.trim() !== '' && !auth.includes('undefined')
    expect(hasRealAuth).toBe(false)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// B1 — navigation: unspecified path edge cases
// ─────────────────────────────────────────────────────────────────────────────

describe('router — adversarial path handling', () => {

  it('[adversarial] [hb:h27] deeply nested unknown path renders, does not crash', () => {
    expect(() => renderRoute('/a/b/c/d/e/f/g/h')).not.toThrow()
    expect(document.body.textContent).not.toBe('')
  })

  it('[adversarial] [hb:h28] unknown path with query string renders without crashing', () => {
    expect(() => renderRoute('/nonexistent?foo=bar&baz=qux')).not.toThrow()
    expect(document.body).toBeDefined()
  })

  it('[adversarial] [hb:h29] unknown path with hash fragment renders without crashing', () => {
    expect(() => renderRoute('/nonexistent#section-anchor')).not.toThrow()
    expect(document.body).toBeDefined()
  })

  it('[adversarial] [hb:h30] path with only a query string renders without crashing', () => {
    expect(() => renderRoute('?key=value')).not.toThrow()
    expect(document.body).toBeDefined()
  })

  it('[adversarial] [hb:h31] URL-encoded path segment does not crash the router', () => {
    expect(() => renderRoute('/%6Cogin')).not.toThrow()
    expect(document.body).toBeDefined()
  })

  it('[adversarial] [hb:h32] null byte in path is handled without crashing', () => {
    expect(() => renderRoute('/path\x00withNull')).not.toThrow()
    expect(document.body).toBeDefined()
  })

  it('[adversarial] [hb:h33] excessively long path segment renders, no crash', () => {
    const longPath = '/'.concat('x'.repeat(4096))
    expect(() => renderRoute(longPath)).not.toThrow()
    expect(document.body.textContent).not.toBe('')
  })

  it('[adversarial] [hb:h34] not-found screen has at least one navigation affordance (link or button)', () => {
    renderRoute('/this-path-definitely-does-not-exist-holdout')
    const affordances = document.querySelectorAll('a, button')
    expect(affordances.length).toBeGreaterThan(0)
  })

  it('[adversarial] [hb:h35] root path renders non-empty content', () => {
    renderRoute('/')
    expect(document.body.textContent?.trim()).not.toBe('')
  })

  it('[adversarial] [hb:h36] login path renders non-empty content', () => {
    renderRoute('/login')
    expect(document.body.textContent?.trim()).not.toBe('')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// B4 — config secret safety
// ─────────────────────────────────────────────────────────────────────────────

describe('getConfig — secret safety', () => {

  it('[adversarial] [hb:h37] getConfig does not throw when called', () => {
    expect(() => getConfig()).not.toThrow()
  })

  it('[adversarial] [hb:h38] getConfig does not surface values injected onto globalThis', () => {
    const g = globalThis as Record<string, unknown>
    const sentinel = '__holdout_secret_sentinel_value_49f2a__'
    g.SERVER_SECRET = sentinel
    g.DATABASE_URL = sentinel
    g.__PRIVATE_KEY__ = sentinel
    try {
      const config = getConfig()
      expect(JSON.stringify(config ?? {})).not.toContain(sentinel)
    } finally {
      delete g.SERVER_SECRET
      delete g.DATABASE_URL
      delete g.__PRIVATE_KEY__
    }
  })

  it('[adversarial] [hb:h39] getConfig result has no top-level key named "process" or "env"', () => {
    const config = getConfig()
    if (config && typeof config === 'object') {
      const keys = Object.keys(config)
      expect(keys).not.toContain('process')
      expect(keys).not.toContain('env')
    }
  })

  it('[adversarial] [hb:h40] getConfig does not return the raw Vite env object wholesale', () => {
    const config = getConfig()
    if (config && typeof config === 'object') {
      // If the entire import.meta.env was returned, all Vite internal keys would be present
      const rawViteKeys = ['MODE', 'DEV', 'PROD', 'SSR', 'BASE_URL']
      const returnedKeys = Object.keys(config as object)
      const leaksRawEnv = rawViteKeys.every(k => returnedKeys.includes(k))
      expect(leaksRawEnv).toBe(false)
    }
  })

  it('[adversarial] [hb:h41] getConfig is idempotent — two calls return equivalent values', () => {
    expect(JSON.stringify(getConfig())).toBe(JSON.stringify(getConfig()))
  })
})
```

---

## `.dark-factory/holdouts/frontend-shell/holdout-bridge.json`

```json
[
  { "test_id": "h01", "spec_anchor": "B2" },
  { "test_id": "h02", "spec_anchor": "B2" },
  { "test_id": "h03", "spec_anchor": "B2" },
  { "test_id": "h04", "spec_anchor": "B2" },
  { "test_id": "h05", "spec_anchor": "B2" },
  { "test_id": "h06", "spec_anchor": "B2" },
  { "test_id": "h07", "spec_anchor": "B2" },
  { "test_id": "h08", "spec_anchor": "B2" },
  { "test_id": "h09", "spec_anchor": "B2" },
  { "test_id": "h10", "spec_anchor": "B2" },
  { "test_id": "h11", "spec_anchor": "B2" },
  { "test_id": "h12", "spec_anchor": "B2" },
  { "test_id": "h13", "spec_anchor": "B2" },
  { "test_id": "h14", "spec_anchor": "B2" },
  { "test_id": "h15", "spec_anchor": "B2" },
  { "test_id": "h16", "spec_anchor": "B2" },
  { "test_id": "h17", "spec_anchor": "B2" },
  { "test_id": "h18", "spec_anchor": "B2" },
  { "test_id": "h19", "spec_anchor": "B2" },
  { "test_id": "h20", "spec_anchor": "B3", "leg": "control",   "pair_id": "dp01" },
  { "test_id": "h21", "spec_anchor": "B3", "leg": "rejection", "pair_id": "dp01" },
  { "test_id": "h22", "spec_anchor": "B3" },
  { "test_id": "h23", "spec_anchor": "B3" },
  { "test_id": "h24", "spec_anchor": "B3" },
  { "test_id": "h25", "spec_anchor": "B3" },
  { "test_id": "h26", "spec_anchor": "B3" },
  { "test_id": "h27", "spec_anchor": "B1" },
  { "test_id": "h28", "spec_anchor": "B1" },
  { "test_id": "h29", "spec_anchor": "B1" },
  { "test_id": "h30", "spec_anchor": "B1" },
  { "test_id": "h31", "spec_anchor": "B1" },
  { "test_id": "h32", "spec_anchor": "B1" },
  { "test_id": "h33", "spec_anchor": "B1" },
  { "test_id": "h34", "spec_anchor": "B1" },
  { "test_id": "h35", "spec_anchor": "B1" },
  { "test_id": "h36", "spec_anchor": "B1" },
  { "test_id": "h37", "spec_anchor": "B4" },
  { "test_id": "h38", "spec_anchor": "B4" },
  { "test_id": "h39", "spec_anchor": "B4" },
  { "test_id": "h40", "spec_anchor": "B4" },
  { "test_id": "h41", "spec_anchor": "B4" }
]
```

---

**41 adversarial holdouts across 4 spec regions. Summary of what each surface probes:**

| Region | IDs | What the spec omits that's probed |
|--------|-----|----------------------------------|
| **B2 envelope** | h01–h10 | Malformed JSON, HTTP status vs envelope priority conflicts, ambiguous dual-key envelopes, missing sub-fields, numeric code, null data, empty envelope |
| **B2 error types** | h11–h15 | Non-Error rejection values (string, null, plain object), mutual exclusivity of the two error classes, both are `instanceof Error` |
| **B2 identity** | h16–h19 | `.status`, `.code`, `.message` preservation on `RejectionError`; oversized code not truncated |
| **B3 auth** | h20–h26 | Differential pair (token→header / null→no header), empty string, whitespace-only, CRLF injection, `getSession` throws, `undefined` return |
| **B1 routing** | h27–h36 | Deep nested unknown, query+unknown, hash+unknown, query-only path, percent-encoded segment, null byte, 4096-char segment, not-found has navigation affordance |
| **B4 config** | h37–h41 | No throw on missing env, no globalThis leakage, no raw keys `process`/`env`, does not return the entire `import.meta.env` object, idempotent |
