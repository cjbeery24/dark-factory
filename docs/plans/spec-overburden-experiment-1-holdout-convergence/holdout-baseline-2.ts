Structure doesn't exist yet — confirmed spec-first. Here are the holdout tests:

```typescript
/**
 * Adversarial holdout tests — Frontend Application Shell
 *
 * All tests: [adversarial] or [holdout] + opaque [hb:*] ids.
 * No [AC-N], [FR-N], or [EC-N] tags per QA Holdout rules.
 * Companion spec-anchor mapping lives in holdout-bridge.json.
 *
 * Assumed this file sits at:
 *   .dark-factory/holdouts/frontend-shell/adversarial.test.ts
 *
 * Adjust the two import blocks once the implementation exists.
 *
 * Assumed client contract:
 *   createApiClient({ baseUrl: string, getToken?: () => string | null })
 *     → { get<T>(path: string): Promise<T>, post<T>(path: string, body: unknown): Promise<T> }
 *   On HTTP success: resolves with the unwrapped value of the "data" field.
 *   On HTTP rejection or parse failure: throws; thrown object carries { status, code, message }.
 *   On connectivity failure (fetch throws): throws; thrown object carries no HTTP status.
 *
 * Assumed routing contract:
 *   AppRoutes = the <Routes> tree without a built-in outer router.
 *   If the implementation embeds BrowserRouter inside App, adjust renderAt() to
 *   render App with a patched window.location instead.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// ── Adjust these import paths to match apps/web/src/ layout ─────────────────
import { createApiClient } from '../../../apps/web/src/api/client'
import { AppRoutes } from '../../../apps/web/src/AppRoutes'
// ────────────────────────────────────────────────────────────────────────────

// ── Fetch mock helpers ───────────────────────────────────────────────────────

const TEST_BASE = 'http://api.test.internal'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function rawResponse(body: string | null, status = 200, contentType = 'text/plain'): Response {
  return new Response(body, {
    status,
    headers: { 'Content-Type': contentType },
  })
}

function stubFetch(body: unknown, status = 200) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse(body, status))
}

function stubFetchRaw(body: string | null, status = 200, contentType = 'text/plain') {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(rawResponse(body, status, contentType))
}

function stubFetchThrow(err: Error) {
  vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(err)
}

function client(getToken?: () => string | null) {
  return createApiClient({ baseUrl: TEST_BASE, getToken })
}

// ────────────────────────────────────────────────────────────────────────────

// ═══════════════════════════════════════════════════════════════════════════
// A — Envelope parsing edge cases
// ═══════════════════════════════════════════════════════════════════════════

describe('API client — envelope edge cases', () => {
  beforeEach(() => vi.resetAllMocks())
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h01] HTTP 200 carrying an error envelope is rejected, not resolved as success', async () => {
    stubFetch({ error: { code: 'FORBIDDEN', message: 'access denied' } }, 200)
    await expect(client().get('/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h02] HTTP 200 with empty body does not silently resolve as undefined data', async () => {
    stubFetchRaw('', 200, 'application/json')
    let resolved: unknown
    let threw = false
    try {
      resolved = await client().get('/x')
    } catch {
      threw = true
    }
    if (!threw) {
      // If it resolves, the value must not be undefined — undefined passed off as T is a silent lie
      expect(resolved).not.toBeUndefined()
    }
    // Either path (throw or explicit value) is acceptable; the illegal outcome is resolving undefined
  })

  it('[adversarial] [hb:h03] HTTP 200 with an HTML body (non-JSON) is surfaced as error, not success', async () => {
    stubFetchRaw('<html><body>502 Bad Gateway</body></html>', 200, 'text/html')
    await expect(client().get('/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h04] Truncated / syntactically invalid JSON body with HTTP 200 is surfaced as error', async () => {
    stubFetchRaw('{ "data": { "id": 42', 200, 'application/json')
    await expect(client().get('/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h05] HTTP 200 with { data: null } resolves as success — null is a valid data value', async () => {
    stubFetch({ data: null }, 200)
    // null must NOT be treated as "no data / error"; it is a legal T
    await expect(client().get('/x')).resolves.toBeNull()
  })

  it('[adversarial] [hb:h06] HTTP 500 with a { data: T } body is still surfaced as error (HTTP status overrides)', async () => {
    stubFetch({ data: { id: 1 } }, 500)
    await expect(client().get('/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h07] HTTP 201 with { data: T } is treated as success (2xx family, not just 200)', async () => {
    stubFetch({ data: { created: true } }, 201)
    const result = await client().get('/x')
    expect(result).toEqual(expect.objectContaining({ created: true }))
  })

  it('[adversarial] [hb:h08] HTTP 204 No Content does not throw a JSON SyntaxError', async () => {
    stubFetchRaw(null, 204, 'application/json')
    let thrownName: string | null = null
    try {
      await client().get('/x')
    } catch (e) {
      thrownName = e instanceof SyntaxError ? 'SyntaxError' : 'other'
    }
    // An unguarded .json() on an empty 204 body is the bug we probe for
    expect(thrownName).not.toBe('SyntaxError')
  })

  it('[adversarial] [hb:h09] Error response with { error: null } on a non-200 status is still surfaced as error', async () => {
    stubFetch({ error: null }, 422)
    await expect(client().get('/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h10] Error envelope missing the code field is still surfaced as error', async () => {
    stubFetch({ error: { message: 'Something went wrong' } }, 400)
    await expect(client().get('/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h11] HTTP 200 response with both data AND error fields present does not silently succeed', async () => {
    // Ambiguous envelope — a naive "if data exists, resolve" would swallow the error field
    stubFetch({ data: { id: 99 }, error: { code: 'CONFLICT', message: 'duplicate resource' } }, 200)
    await expect(client().get('/x')).rejects.toThrow()
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// B — Rejection vs connectivity failure are distinguishable
// ═══════════════════════════════════════════════════════════════════════════

describe('API client — rejection vs connectivity failure', () => {
  beforeEach(() => vi.resetAllMocks())
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h12] Rejection error carries the HTTP status code from the backend response', async () => {
    stubFetch({ error: { code: 'NOT_FOUND', message: 'resource not found' } }, 404)
    let err: Record<string, unknown> | undefined
    try {
      await client().get('/x')
    } catch (e) {
      err = e as Record<string, unknown>
    }
    expect(err).toBeDefined()
    const hasStatus =
      err!['status'] === 404 ||
      err!['statusCode'] === 404 ||
      err!['httpStatus'] === 404
    expect(hasStatus).toBe(true)
  })

  it('[adversarial] [hb:h13] Connectivity failure (TypeError) is distinguishable from backend rejection — rejection carries status; connectivity does not', async () => {
    stubFetchThrow(new TypeError('Failed to fetch'))
    let connErr: unknown
    try {
      await client().get('/x')
    } catch (e) {
      connErr = e
    }

    vi.restoreAllMocks()

    stubFetch({ error: { code: 'SERVER_ERROR', message: 'oops' } }, 500)
    let rejErr: Record<string, unknown> | undefined
    try {
      await client().get('/x')
    } catch (e) {
      rejErr = e as Record<string, unknown>
    }

    // Rejection must expose HTTP status
    const rejHasStatus =
      rejErr != null &&
      ('status' in rejErr || 'statusCode' in rejErr || 'httpStatus' in rejErr)
    expect(rejHasStatus).toBe(true)

    // Connectivity failure must NOT masquerade as a backend rejection with a status code
    const connObj = connErr != null && typeof connErr === 'object' ? (connErr as Record<string, unknown>) : {}
    const connHasStatus =
      'status' in connObj || 'statusCode' in connObj || 'httpStatus' in connObj
    expect(connHasStatus).toBe(false)
  })

  it('[adversarial] [hb:h14] AbortError (cancelled request) is distinguishable from a backend rejection', async () => {
    stubFetchThrow(new DOMException('The user aborted a request.', 'AbortError'))
    let abortErr: unknown
    try {
      await client().get('/x')
    } catch (e) {
      abortErr = e
    }

    vi.restoreAllMocks()

    stubFetch({ error: { code: 'UNAUTHORIZED', message: 'no auth' } }, 401)
    let rejErr: unknown
    try {
      await client().get('/x')
    } catch (e) {
      rejErr = e
    }

    // The two error shapes must differ — something distinguishes them
    expect(abortErr).not.toStrictEqual(rejErr)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// C — Authentication header hygiene
// ═══════════════════════════════════════════════════════════════════════════

describe('API client — authentication header hygiene', () => {
  let capturedHeaders: Headers

  function captureStub() {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedHeaders = new Headers(init?.headers as HeadersInit | undefined)
      return jsonResponse({ data: {} }, 200)
    })
  }

  beforeEach(() => {
    vi.resetAllMocks()
    captureStub()
  })
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h15] Client with getToken returning null sends no Authorization header', async () => {
    await client(() => null).get('/data')
    expect(capturedHeaders.has('Authorization')).toBe(false)
  })

  it('[adversarial] [hb:h16] Client constructed without a getToken option sends no Authorization header', async () => {
    await createApiClient({ baseUrl: TEST_BASE }).get('/data')
    expect(capturedHeaders.has('Authorization')).toBe(false)
  })

  it('[adversarial] [hb:h17] Empty-string token is not forwarded — differential: real token IS forwarded', async () => {
    // Control leg: valid token must produce an Authorization header
    await client(() => 'real-token-xyz').get('/data')
    const controlAuth = capturedHeaders.get('Authorization')

    // Adversarial leg: empty string must not be forwarded as "Bearer "
    await client(() => '').get('/data')
    const emptyAuth = capturedHeaders.get('Authorization')

    expect(controlAuth).toBeTruthy()
    expect(emptyAuth).toBeFalsy()
  })

  it('[adversarial] [hb:h18] Whitespace-only token is not forwarded as credentials', async () => {
    await client(() => '   ').get('/data')
    const auth = capturedHeaders.get('Authorization')
    // "Bearer   " is not a real credential — header must be absent or contain no actual token
    if (auth !== null) {
      expect(auth.replace(/\s/g, '').replace(/^Bearer$/i, '')).toBe('')
    } else {
      expect(auth).toBeNull()
    }
  })

  it('[adversarial] [hb:h19] Session cleared between requests — stale token not reused on second request', async () => {
    let liveToken: string | null = 'session-abc'
    const c = client(() => liveToken)

    await c.get('/first')
    const firstAuth = capturedHeaders.get('Authorization')

    liveToken = null

    await c.get('/second')
    const secondAuth = capturedHeaders.get('Authorization')

    // First request: authenticated
    expect(firstAuth).toBeTruthy()
    // Second request: session gone, no credentials fabricated or cached from first call
    expect(secondAuth).toBeFalsy()
  })

  it('[adversarial] [hb:h20] Token containing special characters is forwarded verbatim, not truncated', async () => {
    const specialToken = 'eyJ.tok+en/with=special_chars-and.dots'
    await client(() => specialToken).get('/data')
    const auth = capturedHeaders.get('Authorization') ?? ''
    expect(auth).toContain(specialToken)
  })
})

// ═══════════════════════════════════════════════════════════════════════════
// D — Routing: paths the spec does not enumerate
// ═══════════════════════════════════════════════════════════════════════════

describe('Routing — adversarial path handling', () => {
  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    )
  }

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('[adversarial] [hb:h21] Unknown path with trailing slash renders the not-found screen without crash', () => {
    expect(() => renderAt('/unknown-route/')).not.toThrow()
    // Something visible must render — not-found is not blank
    expect(document.body.textContent!.trim().length).toBeGreaterThan(0)
  })

  it('[adversarial] [hb:h22] Unknown path with query string renders without crash and shows not-found content', () => {
    expect(() => renderAt('/does-not-exist?foo=bar&baz=qux')).not.toThrow()
    expect(document.body.textContent!.trim().length).toBeGreaterThan(0)
  })

  it('[adversarial] [hb:h23] Deep nested unknown path renders not-found, not a blank page', () => {
    expect(() => renderAt('/a/b/c/d/e/f')).not.toThrow()
    expect(document.body.textContent!.trim().length).toBeGreaterThan(0)
  })

  it('[adversarial] [hb:h24] URL-encoded unknown path renders without crash', () => {
    expect(() => renderAt('/not%20a%20known%20route')).not.toThrow()
  })

  it('[adversarial] [hb:h25] Not-found screen contains a link whose href targets the root ("/") route', () => {
    renderAt('/absolutely-not-a-route')
    const links = screen.getAllByRole('link')
    const backLink = links.find((el) => {
      const href = el.getAttribute('href') ?? ''
      return href === '/' || href === '' || /home|back|dashboard/i.test(el.textContent ?? '')
    })
    expect(backLink).toBeDefined()
    if (backLink?.tagName === 'A') {
      const href = (backLink as HTMLAnchorElement).getAttribute('href')
      expect(href === '/' || href === '').toBe(true)
    }
  })

  it('[adversarial] [hb:h26] Hash-fragment-only path does not crash the router', () => {
    expect(() => renderAt('/#')).not.toThrow()
  })
})
```

---

**Holdout bridge manifest** (write alongside the test file at `.dark-factory/holdouts/frontend-shell/holdout-bridge.json`):

```json
[
  { "test_id": "h01",  "spec_anchor": "B2" },
  { "test_id": "h02",  "spec_anchor": "B2" },
  { "test_id": "h03",  "spec_anchor": "B2" },
  { "test_id": "h04",  "spec_anchor": "B2" },
  { "test_id": "h05",  "spec_anchor": "B2" },
  { "test_id": "h06",  "spec_anchor": "B2" },
  { "test_id": "h07",  "spec_anchor": "B2" },
  { "test_id": "h08",  "spec_anchor": "B2" },
  { "test_id": "h09",  "spec_anchor": "B2" },
  { "test_id": "h10",  "spec_anchor": "B2" },
  { "test_id": "h11",  "spec_anchor": "B2" },
  { "test_id": "h12",  "spec_anchor": "B2" },
  { "test_id": "h13",  "spec_anchor": "B2" },
  { "test_id": "h14",  "spec_anchor": "B2" },
  { "test_id": "h15",  "spec_anchor": "B3" },
  { "test_id": "h16",  "spec_anchor": "B3" },
  { "test_id": "h17",  "spec_anchor": "B3", "leg": "rejection", "pair_id": "p01" },
  { "test_id": "h17c", "spec_anchor": "B3", "leg": "control",   "pair_id": "p01" },
  { "test_id": "h18",  "spec_anchor": "B3" },
  { "test_id": "h19",  "spec_anchor": "B3" },
  { "test_id": "h20",  "spec_anchor": "B3" },
  { "test_id": "h21",  "spec_anchor": "B1" },
  { "test_id": "h22",  "spec_anchor": "B1" },
  { "test_id": "h23",  "spec_anchor": "B1" },
  { "test_id": "h24",  "spec_anchor": "B1" },
  { "test_id": "h25",  "spec_anchor": "B1" },
  { "test_id": "h26",  "spec_anchor": "B1" }
]
```

---

**Coverage surface summary:**

| Group | What's probed | Tests |
|---|---|---|
| Envelope parsing | 200-with-error-body, empty body, HTML body, truncated JSON, `data:null`, 500-with-data-body, 201 success, 204 no-content, null error field, missing code, ambiguous data+error | h01–h11 |
| Rejection vs connectivity | Rejection carries HTTP status; `TypeError` fetch throw ≠ rejection; `AbortError` ≠ rejection | h12–h14 |
| Auth header hygiene | null token, no token option, empty string token (differential pair), whitespace token, stale token after session clear, special-char token verbatim | h15–h20 |
| Routing edge paths | Trailing slash, query string, deep nested, URL-encoded, back link actually targets `/`, hash fragment | h21–h26 |

**Notes file** (`.dark-factory/holdouts/frontend-shell/NOTES.md` — one spec gap found):

> **Gap:** B2 says "a connectivity failure is distinguishable from a rejection" but does not define which distinguishing mechanism is required (error subclass, a `type` field, absence of `status`, etc.). Tests h13–h14 probe the *observable* contract by verifying the two errors are not equal and that rejection carries a `status` field while connectivity does not. If the implementation uses a different discriminant, the assertion in h13 may need adjustment — but the gap should be addressed in the spec with an explicit discriminant requirement.
