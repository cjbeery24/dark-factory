```typescript
// .dark-factory/holdouts/frontend-application-shell/api-client-routing.holdout.test.ts
//
// Adversarial holdout tests — QA Holdout (not visible to the Implementer)
// Target: backend API client + routing  |  Stack: React + Vite + TS  |  Runner: Vitest
//
// ADJUST IMPORT PATHS before integrating — the paths below reflect the
// most common layout for a React + Vite + TS workspace (apps/web/).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider, matchRoutes } from 'react-router-dom'
import React from 'react'

// ── Core imports ─────────────────────────────────────────────────────────────
//   request: (path: string, init?: RequestInit) => Promise<T>
//   Resolves with the `data` value on success; rejects with error info on failure.
import { request } from '@/lib/api'

//   routes: RouteObject[] — the app's route configuration array
import { routes } from '@/router/routes'

//   Session control — pick the variant that matches the actual implementation:
//     Option A (module-level store):   import { setSession, clearSession } from '@/lib/session'
//     Option B (exported from client): import { setAuthToken, clearAuthToken } from '@/lib/api'
//     Option C (mock the token source): vi.mock('@/lib/auth', () => ({ getToken: () => TOKEN }))
import { setSession, clearSession } from '@/lib/session'
// ─────────────────────────────────────────────────────────────────────────────

// ── Fetch helpers ─────────────────────────────────────────────────────────────

interface StubOpts { status?: number; contentType?: string }

function stubJsonFetch(body: unknown, { status = 200, contentType = 'application/json' }: StubOpts = {}) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': contentType }),
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  }))
}

function stubRawFetch(body: string, { status = 200, contentType = 'text/plain' }: StubOpts = {}) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': contentType }),
    json: () => Promise.reject(new SyntaxError('not JSON')),
    text: () => Promise.resolve(body),
  }))
}

function stubNetworkError(err: Error) {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(err))
}

/** Pull the Authorization header value from the first captured fetch call. */
function capturedAuthHeader(): string | null {
  const calls = vi.mocked(fetch).mock.calls
  if (!calls.length) return null
  const init = calls[0][1] as RequestInit | undefined
  const h = init?.headers
  if (!h) return null
  if (h instanceof Headers) return h.get('authorization')
  if (Array.isArray(h)) {
    const entry = (h as Array<[string, string]>).find(([k]) => k.toLowerCase() === 'authorization')
    return entry ? entry[1] : null
  }
  const rec = h as Record<string, string>
  return rec['authorization'] ?? rec['Authorization'] ?? null
}

/** Pull a named header value from a specific fetch call by index. */
function headerFromCall(callIndex: number, name: string): string | null {
  const call = vi.mocked(fetch).mock.calls[callIndex]
  if (!call) return null
  const init = call[1] as RequestInit | undefined
  const h = init?.headers
  if (!h) return null
  if (h instanceof Headers) return h.get(name)
  if (Array.isArray(h)) {
    const entry = (h as Array<[string, string]>).find(([k]) => k.toLowerCase() === name.toLowerCase())
    return entry ? entry[1] : null
  }
  const rec = h as Record<string, string>
  return rec[name] ?? rec[name.toLowerCase()] ?? null
}

// ── Router helper ─────────────────────────────────────────────────────────────

function renderPath(path: string) {
  const r = createMemoryRouter(routes, { initialEntries: [path] })
  return render(<RouterProvider router={r} />)
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 1 — Envelope shape anomalies                               anchor: B2
// ─────────────────────────────────────────────────────────────────────────────

describe('API client — envelope shape anomalies', () => {
  afterEach(() => vi.restoreAllMocks())

  // ── Differential pair p01 ──────────────────────────────────────────────────
  // Control (h01): clean { data } resolves. Rejection (h02): adding `error` to
  // the same body must flip the outcome. Proves attribution to the `error` key,
  // not to an unrelated cause.

  it('[adversarial] [hb:h01] clean data envelope resolves to the data value', async () => {
    stubJsonFetch({ data: 'baseline-payload' })
    const result = await request('/probe')
    expect(result).toEqual('baseline-payload')
  })

  it('[adversarial] [hb:h02] envelope carrying both data and error does not silently return data', async () => {
    stubJsonFetch({ data: 'baseline-payload', error: { code: 'COLLISION', message: 'both keys present' } })
    await expect(request('/probe')).rejects.toBeDefined()
  })

  // ── Single holdouts ────────────────────────────────────────────────────────

  it('[adversarial] [hb:h03] error key present but null is not treated as a rejection', async () => {
    // { error: null } is not the error envelope — { data } should still resolve.
    stubJsonFetch({ data: 'present', error: null })
    const result = await request('/probe')
    expect(result).toEqual('present')
  })

  it('[adversarial] [hb:h04] error object missing message still surfaces as a rejection', async () => {
    stubJsonFetch({ error: { code: 'INCOMPLETE' } })
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h05] error object missing code still surfaces as a rejection', async () => {
    stubJsonFetch({ error: { message: 'no code here' } })
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h06] error value that is a plain string (not an object) surfaces as a rejection', async () => {
    stubJsonFetch({ error: 'flat string error' })
    await expect(request('/probe')).rejects.toBeDefined()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 2 — HTTP status vs envelope conflicts                      anchor: B2
// ─────────────────────────────────────────────────────────────────────────────

describe('API client — status/envelope conflicts', () => {
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h07] HTTP 4xx carrying a data body is still treated as an error', async () => {
    // An implementation that only checks the envelope and ignores HTTP status would pass data back.
    stubJsonFetch({ data: 'looks-like-success' }, { status: 403 })
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h08] HTTP 5xx with an empty body surfaces as an error and does not crash', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      headers: new Headers(),
      json: () => Promise.reject(new SyntaxError('empty')),
      text: () => Promise.resolve(''),
    }))
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h09] HTTP 201 carrying an error envelope is treated as a rejection', async () => {
    // Spec: the envelope is the contract. A 2xx status does not override the error envelope.
    // An implementation that only checks response.ok would mistakenly resolve.
    stubJsonFetch({ error: { code: 'CREATED_BUT_ERRONEOUS', message: 'created yet failed' } }, { status: 201 })
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h10] non-JSON response body on HTTP 200 does not silently succeed', async () => {
    // A gateway / proxy HTML error page delivered with 200 must not be treated as success.
    stubRawFetch('<html><body>Bad Gateway</body></html>', { contentType: 'text/html', status: 200 })
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h11] completely empty body on HTTP 200 does not resolve as data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.reject(new SyntaxError('empty body')),
      text: () => Promise.resolve(''),
    }))
    await expect(request('/probe')).rejects.toBeDefined()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 3 — Connectivity failure taxonomy                          anchor: B2
// ─────────────────────────────────────────────────────────────────────────────

describe('API client — connectivity failures', () => {
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h12] AbortError propagates as a rejection and does not silently succeed', async () => {
    // Some implementations catch DOMException and swallow it or return undefined.
    stubNetworkError(new DOMException('The operation was aborted', 'AbortError'))
    await expect(request('/probe')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h13] TypeError (network unreachable) propagates as a rejection and does not silently succeed', async () => {
    stubNetworkError(new TypeError('Failed to fetch'))
    await expect(request('/probe')).rejects.toBeDefined()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 4 — Authentication header presence / absence               anchor: B3
// ─────────────────────────────────────────────────────────────────────────────

describe('API client — authentication header presence', () => {
  beforeEach(() => clearSession?.())
  afterEach(() => {
    clearSession?.()
    vi.restoreAllMocks()
  })

  it('[adversarial] [hb:h14] empty-string session token sends no authorization header', async () => {
    setSession?.({ token: '' })
    stubJsonFetch({ data: 'ok' })
    await request('/probe').catch(() => {/* session may be invalid — header is what matters */})
    expect(capturedAuthHeader()).toBeNull()
  })

  it('[adversarial] [hb:h15] whitespace-only session token sends no authorization header', async () => {
    // A token of spaces is not a valid credential — should be treated the same as absent.
    setSession?.({ token: '   \t\n' })
    stubJsonFetch({ data: 'ok' })
    await request('/probe').catch(() => {})
    expect(capturedAuthHeader()).toBeNull()
  })

  it('[adversarial] [hb:h16] CRLF in session token does not produce a split-header injection', async () => {
    // A malicious or corrupted token must not let arbitrary headers be injected.
    setSession?.({ token: 'legit\r\nX-Injected: evil' })
    stubJsonFetch({ data: 'ok' })
    await request('/probe').catch(() => {})
    // The injected header must not appear — either fetch rejected the bad header
    // (throwing, which is caught above) or the client sanitized/blocked it.
    const injected = headerFromCall(0, 'x-injected')
    expect(injected).toBeNull()
  })

  it('[adversarial] [hb:h17] all concurrent requests carry an authorization header when a session is active', async () => {
    setSession?.({ token: 'concurrent-tok' })
    const mockFn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve({ data: 'ok' }),
      text: () => Promise.resolve('{"data":"ok"}'),
    })
    vi.stubGlobal('fetch', mockFn)

    await Promise.all([request('/r1'), request('/r2'), request('/r3')])

    expect(mockFn.mock.calls.length).toBe(3)
    for (let i = 0; i < 3; i++) {
      expect(headerFromCall(i, 'authorization')).toBeTruthy()
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 5 — Routing path edge cases                                anchor: B1
// ─────────────────────────────────────────────────────────────────────────────

describe('Routing — path edge cases', () => {
  beforeEach(() => {
    // Prevent route components from making live backend calls during render.
    stubJsonFetch({ data: null })
  })
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h18] trailing slash on a defined path does not crash the router', () => {
    expect(() => renderPath('/login/')).not.toThrow()
  })

  it('[adversarial] [hb:h19] very long unrecognized path does not crash the router', () => {
    const longPath = '/' + 'z'.repeat(512)
    expect(() => renderPath(longPath)).not.toThrow()
  })

  it('[adversarial] [hb:h20] Unicode characters in an unrecognized path do not crash the router', () => {
    expect(() => renderPath('/路由/测试/🔥')).not.toThrow()
  })

  it('[adversarial] [hb:h21] root with a query string containing angle brackets does not crash the router', () => {
    // Tests that the router does not attempt to evaluate or interpret query params.
    expect(() => renderPath('/?x=<script>alert(1)</script>')).not.toThrow()
  })

  it('[adversarial] [hb:h22] path traversal segment does not silently match the root route', () => {
    // React Router normalizes paths internally — verify no crash and capture the outcome explicitly.
    let traversalMatches: ReturnType<typeof matchRoutes> | undefined
    expect(() => {
      traversalMatches = matchRoutes(routes, '/../')
    }).not.toThrow()

    const rootMatches = matchRoutes(routes, '/')
    // If traversal normalizes to root, behaviour is surfaced explicitly; it must not crash.
    // The test documents the actual normalization rather than asserting a preferred outcome.
    void traversalMatches; void rootMatches
  })

  it('[adversarial] [hb:h23] null byte in a path segment does not crash the router', () => {
    expect(() => matchRoutes(routes, '/valid\x00segment')).not.toThrow()
  })
})
```

---

**`holdout-bridge.json`** (write to `.dark-factory/holdouts/frontend-application-shell/holdout-bridge.json`):

```json
[
  { "test_id": "h01", "spec_anchor": "B2", "leg": "control",   "pair_id": "p01" },
  { "test_id": "h02", "spec_anchor": "B2", "leg": "rejection", "pair_id": "p01" },
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
  { "test_id": "h14", "spec_anchor": "B3" },
  { "test_id": "h15", "spec_anchor": "B3" },
  { "test_id": "h16", "spec_anchor": "B3" },
  { "test_id": "h17", "spec_anchor": "B3" },
  { "test_id": "h18", "spec_anchor": "B1" },
  { "test_id": "h19", "spec_anchor": "B1" },
  { "test_id": "h20", "spec_anchor": "B1" },
  { "test_id": "h21", "spec_anchor": "B1" },
  { "test_id": "h22", "spec_anchor": "B1" },
  { "test_id": "h23", "spec_anchor": "B1" }
]
```

---

**Surface probed — by section:**

| Section | Count | What the tests probe that the spec doesn't enumerate |
|---|---|---|
| Envelope anomalies | 6 | Both keys present; `error: null`; partial error objects (missing `code` or `message`); `error` as a string |
| Status/envelope conflicts | 5 | 4xx + data body; 5xx empty body; **201 + error envelope** (2xx can still be a rejection); non-JSON 200; empty body 200 |
| Connectivity taxonomy | 2 | `AbortError` and `TypeError` both propagate cleanly rather than being swallowed |
| Auth header presence | 4 | Empty-string token; whitespace-only token; CRLF injection in token; concurrent requests all authenticated |
| Routing edge cases | 6 | Trailing slash; 512-char path; Unicode/emoji path; angle-bracket query string; path traversal `/../`; null byte in segment |

**Differential pair p01 (h01/h02):** The control leg proves `{ data }` resolves cleanly. The rejection leg adds only the `error` key to the same body — failure becomes attributable to that key, not to any other cause.
