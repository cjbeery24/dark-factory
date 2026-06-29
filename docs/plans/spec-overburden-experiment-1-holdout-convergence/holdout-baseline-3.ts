```typescript
/**
 * Adversarial holdout tests — Frontend Application Shell
 *
 * HOLDOUT BRIDGE MANIFEST
 * (Materialize to: .dark-factory/holdouts/frontend-shell/holdout-bridge.json)
 *
 * [
 *   { "test_id": "h01", "spec_anchor": "B2" },
 *   { "test_id": "h02", "spec_anchor": "B2" },
 *   { "test_id": "h03", "spec_anchor": "B2" },
 *   { "test_id": "h04", "spec_anchor": "B2" },
 *   { "test_id": "h05", "spec_anchor": "B2" },
 *   { "test_id": "h06", "spec_anchor": "B2" },
 *   { "test_id": "h07", "spec_anchor": "B2" },
 *   { "test_id": "h08", "spec_anchor": "B2" },
 *   { "test_id": "h09", "spec_anchor": "B2" },
 *   { "test_id": "h10", "spec_anchor": "B2" },
 *   { "test_id": "h11", "spec_anchor": "B3", "leg": "control",   "pair_id": "dp1" },
 *   { "test_id": "h12", "spec_anchor": "B3", "leg": "rejection", "pair_id": "dp1" },
 *   { "test_id": "h13", "spec_anchor": "B3", "leg": "control",   "pair_id": "dp2" },
 *   { "test_id": "h14", "spec_anchor": "B3", "leg": "rejection", "pair_id": "dp2" },
 *   { "test_id": "h15", "spec_anchor": "B1" },
 *   { "test_id": "h16", "spec_anchor": "B1" },
 *   { "test_id": "h17", "spec_anchor": "B1" },
 *   { "test_id": "h18", "spec_anchor": "B1" },
 *   { "test_id": "h19", "spec_anchor": "B4" },
 *   { "test_id": "h20", "spec_anchor": "B2" }
 * ]
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// ---------------------------------------------------------------------------
// Import paths — adjust to actual module locations in this workspace.
//
// apiRequest must:
//   • resolve with the unwrapped T on { "data": T }
//   • reject with an error carrying the backend's { code, message } on { "error": {...} }
//   • reject with a distinguishable error when fetch() itself throws (network failure)
//
// Token option key may differ (authToken, authorization, headers.Authorization, …).
// If the client reads from a context/store instead of accepting a parameter, the
// dp1/dp2 describe blocks need a matching setup shim.
// ---------------------------------------------------------------------------
import { apiRequest } from '@/lib/api'

// If App embeds its own <BrowserRouter>, either:
//   (a) export a <Routes>-only component for tests and import that, or
//   (b) swap BrowserRouter for MemoryRouter via a test-env flag.
import App from '@/App'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonResp(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function rawResp(text: string, status = 200, ct = 'text/html'): Response {
  return new Response(text, { status, headers: { 'Content-Type': ct } })
}

/** Extract the Authorization header value from whatever fetch() received. */
function capturedAuthHeader(
  calls: [RequestInfo | URL, RequestInit?][]
): string | undefined {
  const [reqArg, reqInit] = calls[0] ?? []
  if (reqArg instanceof Request) {
    return reqArg.headers.get('authorization') ?? reqArg.headers.get('Authorization') ?? undefined
  }
  const h = (reqInit as RequestInit | undefined)?.headers
  if (!h) return undefined
  if (h instanceof Headers) return h.get('authorization') ?? undefined
  const rec = h as Record<string, string>
  return rec['authorization'] ?? rec['Authorization']
}

function renderAtPath(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  )
}

// ---------------------------------------------------------------------------
// B2 — envelope and error surfacing (adversarial)
// ---------------------------------------------------------------------------

describe('API client — envelope and error surfacing', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(global, 'fetch')
  })
  afterEach(() => {
    fetchSpy.mockRestore()
  })

  it('[adversarial] [hb:h01] { "data": null } resolves as success with null — null is valid data', async () => {
    fetchSpy.mockResolvedValueOnce(jsonResp({ data: null }))
    const result = await apiRequest('/resource')
    expect(result).toBeNull()
  })

  it('[adversarial] [hb:h02] body with both "data" and "error" keys is not silently treated as success', async () => {
    fetchSpy.mockResolvedValueOnce(
      jsonResp({ data: { id: 1 }, error: { code: 'CONFLICT', message: 'conflict' } })
    )
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h03] 200 with a non-JSON body surfaces as an error, never as data', async () => {
    fetchSpy.mockResolvedValueOnce(rawResp('<html><body>Gateway error</body></html>', 200))
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h04] 200 with a completely empty body surfaces as an error', async () => {
    fetchSpy.mockResolvedValueOnce(new Response('', { status: 200 }))
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h05] error envelope with empty object { "error": {} } still surfaces as error', async () => {
    fetchSpy.mockResolvedValueOnce(jsonResp({ error: {} }))
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h06] error envelope where the error value is null is not treated as success', async () => {
    // The "error" key is present; its null value does not promote to { "data": ... }
    fetchSpy.mockResolvedValueOnce(jsonResp({ error: null }))
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h07] backend rejection carries backend identity; network failure is a distinct type', async () => {
    // — backend rejection —
    fetchSpy.mockResolvedValueOnce(
      jsonResp({ error: { code: 'FORBIDDEN', message: 'access denied' } }, 403)
    )
    let rejErr: unknown
    try { await apiRequest('/guarded') } catch (e) { rejErr = e }

    expect(rejErr).toBeDefined()
    const rej = rejErr as Record<string, unknown>
    // Must carry the backend's code and/or message
    expect(Boolean(rej['code'] ?? rej['message'] ?? rej['status'])).toBe(true)

    // — network failure —
    fetchSpy.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    let netErr: unknown
    try { await apiRequest('/resource') } catch (e) { netErr = e }

    expect(netErr).toBeDefined()
    const net = netErr as Record<string, unknown>
    // Must be distinguishable from the backend rejection above
    const distinguishable =
      netErr instanceof TypeError ||
      net['type'] === 'network' ||
      net['type'] === 'connectivity' ||
      // Different shape: lacks the backend's code/message identity
      (net['code'] !== 'FORBIDDEN' && net['message'] !== 'access denied')
    expect(distinguishable).toBe(true)
  })

  it('[adversarial] [hb:h08] 4xx status with a success-shaped body is not resolved as data', async () => {
    // Whether the client gates on HTTP status or on the envelope shape, a 4xx is a
    // backend rejection and must not resolve.
    fetchSpy.mockResolvedValueOnce(jsonResp({ data: { id: 99 } }, 400))
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h09] 200 status with an error-shaped envelope surfaces as an error', async () => {
    // Spec: "The success/error envelope is the backend's contract" — the envelope wins
    // over the HTTP status code.
    fetchSpy.mockResolvedValueOnce(
      jsonResp({ error: { code: 'GONE', message: 'resource permanently removed' } }, 200)
    )
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })

  it('[adversarial] [hb:h10] 204 No Content neither hangs nor throws synchronously — settles within 2 s', async () => {
    fetchSpy.mockResolvedValueOnce(new Response(null, { status: 204 }))
    const settled = await Promise.race([
      apiRequest('/resource').then(() => 'resolved').catch(() => 'rejected'),
      new Promise<string>((_, reject) =>
        setTimeout(() => reject(new Error('timed out after 2 s')), 2000)
      ),
    ])
    expect(['resolved', 'rejected']).toContain(settled)
  })

  it('[adversarial] [hb:h20] truncated JSON body is surfaced as an error', async () => {
    fetchSpy.mockResolvedValueOnce(
      rawResp('{ "data":', 200, 'application/json')
    )
    await expect(apiRequest('/resource')).rejects.toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// B3 — authentication header differential pairs
//
// dp1: valid token (CONTROL) vs explicitly absent session (REJECTION)
//      — verifies the "no session → no credentials" guarantee
// dp2: valid non-empty token (CONTROL) vs whitespace-only token (REJECTION)
//      — verifies the client does not normalise "   ".trim() === "" into a sent credential
// ---------------------------------------------------------------------------

describe('API client — authentication header differential pairs', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(global, 'fetch')
    fetchSpy.mockResolvedValue(jsonResp({ data: 'ok' }))
  })
  afterEach(() => {
    fetchSpy.mockRestore()
  })

  // — dp1 control —
  it('[adversarial] [hb:h11] (dp1 control) valid session token causes Authorization header to be sent', async () => {
    await apiRequest('/resource', { token: 'valid-session-abc123' }).catch(() => {})
    const auth = capturedAuthHeader(fetchSpy.mock.calls as [RequestInfo | URL, RequestInit?][])
    expect(auth).toBeDefined()
    expect(auth).toContain('valid-session-abc123')
  })

  // — dp1 rejection —
  it('[adversarial] [hb:h12] (dp1 rejection) absent session sends no Authorization header and fabricates no credential', async () => {
    await apiRequest('/resource', { token: undefined }).catch(() => {})
    const auth = capturedAuthHeader(fetchSpy.mock.calls as [RequestInfo | URL, RequestInit?][])
    expect(auth).toBeUndefined()
  })

  // — dp2 control —
  it('[adversarial] [hb:h13] (dp2 control) non-empty non-whitespace token appears verbatim in Authorization header', async () => {
    await apiRequest('/resource', { token: 'session-xyz' }).catch(() => {})
    const auth = capturedAuthHeader(fetchSpy.mock.calls as [RequestInfo | URL, RequestInit?][])
    expect(auth).toBeDefined()
    expect(auth).toContain('session-xyz')
  })

  // — dp2 rejection —
  it('[adversarial] [hb:h14] (dp2 rejection) whitespace-only token must not be normalised into a sent credential', async () => {
    // Client MUST NOT: trim("   ") → "" → send "Bearer " as a valid auth header.
    // Either the header is absent, or it contains the raw whitespace (i.e. was not stripped).
    // It must NOT match the form of a real bearer credential.
    await apiRequest('/resource', { token: '   ' }).catch(() => {})
    const auth = capturedAuthHeader(fetchSpy.mock.calls as [RequestInfo | URL, RequestInit?][])
    if (auth !== undefined) {
      // If a header was sent at all, it must not look like a legitimate "Bearer <token>"
      expect(auth.trim()).not.toMatch(/^Bearer \S+/)
    }
    // auth === undefined → client correctly omitted the header ✓
  })
})

// ---------------------------------------------------------------------------
// B1 — routing adversarial paths
// ---------------------------------------------------------------------------

describe('Routing — adversarial paths', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('[adversarial] [hb:h15] deeply nested unknown path provides a navigation affordance back to dashboard', () => {
    renderAtPath('/a/b/c/d/e/f')
    // The not-found screen must offer a way back; if nothing at all renders, that is also a failure.
    const backAffordance =
      screen.queryByRole('link',   { name: /home|dashboard|back/i }) ??
      screen.queryByRole('button', { name: /home|dashboard|back|go back/i })
    expect(backAffordance).not.toBeNull()
  })

  it('[adversarial] [hb:h16] root path with a query string renders the dashboard route, not a not-found screen', () => {
    renderAtPath('/?utm_source=email&ref=banner')
    // Query params must not perturb route matching; a not-found back-link must be absent.
    const notFoundAffordance =
      screen.queryByRole('link',   { name: /home|dashboard|back/i }) ??
      screen.queryByRole('button', { name: /home|dashboard|back|go back/i })
    // Presence of this affordance signals the router misrouted "/" with params to 404.
    // Absence confirms the dashboard (or at least not the not-found screen) rendered.
    expect(document.body.innerHTML.trim().length).toBeGreaterThan(0)
    // The not-found affordance must not be the ONLY interactive element visible at "/"
    const allInteractive = screen.queryAllByRole('link').concat(screen.queryAllByRole('button'))
    if (notFoundAffordance !== null && allInteractive.length === 1) {
      // Only a "go back" link rendered — that's the 404 screen, not the dashboard.
      throw new Error('Only a not-found back-link rendered for / with query params — router misrouted.')
    }
  })

  it('[adversarial] [hb:h17] root path with a fragment renders the dashboard route without crashing', () => {
    renderAtPath('/#section-anchor')
    // Fragment must be ignored by the client-side router for route matching.
    expect(document.body.innerHTML.trim().length).toBeGreaterThan(0)
    const allInteractive = screen.queryAllByRole('link').concat(screen.queryAllByRole('button'))
    const notFoundAffordance =
      screen.queryByRole('link',   { name: /home|dashboard|back/i }) ??
      screen.queryByRole('button', { name: /home|dashboard|back|go back/i })
    if (notFoundAffordance !== null && allInteractive.length === 1) {
      throw new Error('Only a not-found back-link rendered for /#fragment — router misrouted.')
    }
  })

  it('[adversarial] [hb:h18] percent-encoded unknown path decodes correctly and shows not-found with navigation back', () => {
    // /p%61ge decodes to /page — an unrecognised route that must produce the not-found screen.
    renderAtPath('/p%61ge')
    // Must render something (no blank/crashed screen)
    expect(document.body.innerHTML.trim().length).toBeGreaterThan(0)
    // Not-found screen must provide navigation back
    const backAffordance =
      screen.queryByRole('link',   { name: /home|dashboard|back/i }) ??
      screen.queryByRole('button', { name: /home|dashboard|back|go back/i })
    expect(backAffordance).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// B4 — secret safety: backend address from build-time config
// ---------------------------------------------------------------------------

describe('Secret safety — backend address', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    fetchSpy = vi.spyOn(global, 'fetch')
    fetchSpy.mockResolvedValue(jsonResp({ data: 'ok' }))
  })
  afterEach(() => {
    fetchSpy.mockRestore()
  })

  it('[adversarial] [hb:h19] requests use the VITE_API_URL base and embed no secret-material patterns in the URL', async () => {
    await apiRequest('/probe').catch(() => {})

    // If the client deferred (e.g. no config, no request made) this probe is inconclusive — skip.
    if (fetchSpy.mock.calls.length === 0) return

    const url = String(fetchSpy.mock.calls[0][0] ?? '')

    // When the env var is present, it must be reflected in the request URL.
    const configured = import.meta.env['VITE_API_URL'] as string | undefined
    if (configured) {
      expect(url).toContain(configured)
    }

    // Must not embed credentials or key material directly in the URL itself.
    // (Server-only env vars are stripped by Vite at build time and must never reach the browser.)
    expect(url).not.toMatch(/[?&](key|secret|password|token)=[A-Za-z0-9_.%-]{8,}/i)
    // Must not embed userinfo (Basic auth) in the URL authority.
    expect(url).not.toMatch(/^https?:\/\/[^@\s]+:[^@\s]+@/)
  })
})
```
