The write needs your approval. While you decide, here is the complete test file and bridge manifest — the full "stdout" output as requested:

---

### `.dark-factory/holdouts/frontend-shell/holdout.test.ts`

```typescript
/**
 * Adversarial holdout tests — Frontend Application Shell spec
 * Tags: [adversarial] [hb:<id>]  — NO AC-N / FR-N / EC-N tags.
 * Bridge: holdout-bridge.json
 *
 * Interface surface (from Architect manifest, test_integration):
 *   createApiClient({ fetchFn, getSession }) → client with .request(path, init?) method
 *   RejectionError — thrown when backend responds with an error envelope or 4xx/5xx
 *   ConnectivityError — thrown when fetch itself throws (network unreachable)
 *   routes — React Router route definition array
 *   getConfig() — reads only VITE_-prefixed import.meta.env keys
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  createApiClient,
  RejectionError,
  ConnectivityError,
} from '../../../apps/web/src/lib/apiClient.js'
import { routes } from '../../../apps/web/src/router.js'
import { getConfig } from '../../../apps/web/src/config.js'
import React from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { render, screen } from '@testing-library/react'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function rawRes(status: number, text: string, contentType = 'text/plain'): Response {
  return new Response(text, { status, headers: { 'Content-Type': contentType } })
}

type Session = { token: string } | null | undefined

function makeClient(fetchFn: typeof fetch, getSession: () => Session = () => null) {
  return createApiClient({ fetchFn, getSession })
}

function captureHeadersFetch(stub: Response) {
  let captured = new Headers()
  const fetchFn = vi.fn((_url: unknown, init?: RequestInit) => {
    captured = new Headers(init?.headers as HeadersInit | undefined)
    return Promise.resolve(stub)
  }) as unknown as typeof fetch
  return { fetchFn, headers: () => captured }
}

function routeRender(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return render(<RouterProvider router={router} />)
}

// ---------------------------------------------------------------------------
// B2 — envelope parsing and error surfacing
// ---------------------------------------------------------------------------

describe('apiClient adversarial — envelope and error surfacing', () => {
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h01] non-JSON body on HTTP 200 is surfaced as an error, never silent success', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      rawRes(200, '<html>502 Bad Gateway</html>', 'text/html'),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h02] HTTP 200 carrying an error-keyed envelope surfaces as RejectionError', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(200, { error: { code: 'E_GHOST', message: 'ghost error inside 200' } }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h03] response with both data and error keys does not throw an untyped internal error', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(200, { data: { id: 1 }, error: { code: 'E_AMBIG', message: 'both keys' } }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    const err = await client.request('/api/x').catch((e: unknown) => e)
    if (err instanceof Error) {
      expect(err).toBeInstanceOf(RejectionError)
    }
  })

  it('[adversarial] [hb:h04] empty-string response body on HTTP 200 is not silently treated as success', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      rawRes(200, '', 'application/json'),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h05] AbortError thrown by fetch surfaces as ConnectivityError', async () => {
    const abortErr = Object.assign(new Error('signal aborted'), { name: 'AbortError' })
    const fetchFn = vi.fn().mockRejectedValue(abortErr) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toBeInstanceOf(ConnectivityError)
  })

  it('[adversarial] [hb:h06] generic Error thrown by fetch surfaces as ConnectivityError', async () => {
    const fetchFn = vi.fn().mockRejectedValue(
      new Error('unknown network failure'),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toBeInstanceOf(ConnectivityError)
  })

  it('[adversarial] [hb:h13] error envelope with missing code AND message still surfaces as RejectionError', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(400, { error: {} }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h14] 4xx with non-JSON body surfaces as RejectionError not ConnectivityError', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      rawRes(403, 'Forbidden'),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    const err = await client.request('/api/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(RejectionError)
    expect(err).not.toBeInstanceOf(ConnectivityError)
  })

  it('[adversarial] [hb:h15] HTTP 204 No Content does not propagate as a raw SyntaxError', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      new Response(null, { status: 204 }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    const err = await client.request('/api/x').catch((e: unknown) => e)
    if (err instanceof Error) {
      expect(err).not.toBeInstanceOf(SyntaxError)
    }
  })

  it('[adversarial] [hb:h16] HTTP 201 Created with data envelope resolves with the unwrapped payload', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(201, { data: { created: true } }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).resolves.toMatchObject({ created: true })
  })

  it('[adversarial] [hb:h17] HTTP 422 rejection carries status 422 on the thrown RejectionError', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(422, { error: { code: 'VALIDATION_FAILED', message: 'bad input' } }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    const err = await client.request('/api/x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(RejectionError)
    expect((err as RejectionError).status).toBe(422)
  })

  it('[adversarial] [hb:h18] RejectionError and ConnectivityError are mutually exclusive types', async () => {
    const rejFetch = vi.fn().mockResolvedValue(
      jsonRes(400, { error: { code: 'E', message: 'm' } }),
    ) as unknown as typeof fetch
    const connFetch = vi.fn().mockRejectedValue(
      new TypeError('Failed to fetch'),
    ) as unknown as typeof fetch

    const [rejErr, connErr] = await Promise.all([
      makeClient(rejFetch).request('/api/x').catch((e: unknown) => e),
      makeClient(connFetch).request('/api/x').catch((e: unknown) => e),
    ])

    expect(rejErr).toBeInstanceOf(RejectionError)
    expect(connErr).toBeInstanceOf(ConnectivityError)
    expect(rejErr).not.toBeInstanceOf(ConnectivityError)
    expect(connErr).not.toBeInstanceOf(RejectionError)
  })

  it('[adversarial] [hb:h22] 4xx with error key set to null is not silently treated as success', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(400, { error: null }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h24] response body that is a JSON array is not silently treated as success', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(200, [1, 2, 3]),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toThrow()
  })

  it('[adversarial] [hb:h25] response body that is JSON null is not silently treated as success', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      new Response('null', { status: 200, headers: { 'Content-Type': 'application/json' } }),
    ) as unknown as typeof fetch
    const client = makeClient(fetchFn)
    await expect(client.request('/api/x')).rejects.toThrow()
  })
})

// ---------------------------------------------------------------------------
// B3 — credential attachment
// ---------------------------------------------------------------------------

describe('apiClient adversarial — credential attachment', () => {
  afterEach(() => vi.restoreAllMocks())

  it('[adversarial] [hb:h07] null session produces no Authorization header on the outbound request', async () => {
    const { fetchFn, headers } = captureHeadersFetch(jsonRes(200, { data: {} }))
    await makeClient(fetchFn, () => null).request('/api/x')
    expect(headers().has('authorization')).toBe(false)
  })

  it('[adversarial] [hb:h08] undefined session produces no Authorization header on the outbound request', async () => {
    const { fetchFn, headers } = captureHeadersFetch(jsonRes(200, { data: {} }))
    await makeClient(fetchFn, () => undefined).request('/api/x')
    expect(headers().has('authorization')).toBe(false)
  })

  it('[adversarial] [hb:h09] empty-string token does not produce a non-empty Authorization header', async () => {
    const { fetchFn, headers } = captureHeadersFetch(jsonRes(200, { data: {} }))
    await makeClient(fetchFn, () => ({ token: '' })).request('/api/x')
    const auth = headers().get('authorization') ?? ''
    expect(auth.trim()).toBe('')
  })

  it('[adversarial] [hb:h10] whitespace-only token is not trimmed and replaced with a fabricated credential', async () => {
    const { fetchFn, headers } = captureHeadersFetch(jsonRes(200, { data: {} }))
    await makeClient(fetchFn, () => ({ token: '   ' })).request('/api/x')
    const auth = headers().get('authorization') ?? ''
    if (auth.length > 0) {
      expect(auth).toContain('   ')
    }
  })

  it('[adversarial] [hb:h11] synchronous throw from getSession propagates to the caller, not swallowed', async () => {
    const fetchFn = vi.fn().mockResolvedValue(
      jsonRes(200, { data: {} }),
    ) as unknown as typeof fetch
    const client = createApiClient({
      fetchFn,
      getSession: () => { throw new Error('storage unavailable') },
    })
    await expect(client.request('/api/x')).rejects.toThrow('storage unavailable')
  })

  it('[adversarial] [hb:h12] token containing colons is transmitted verbatim without splitting on colon', async () => {
    const rawToken = 'user:secretpart:extra'
    const allHeaderValues: string[] = []
    const fetchFn = vi.fn((_url: unknown, init?: RequestInit) => {
      const h = new Headers(init?.headers as HeadersInit | undefined)
      h.forEach((v) => allHeaderValues.push(v))
      return Promise.resolve(jsonRes(200, { data: {} }))
    }) as unknown as typeof fetch
    await makeClient(fetchFn, () => ({ token: rawToken })).request('/api/x')
    expect(allHeaderValues.join(' ')).toContain(rawToken)
  })

  it('[adversarial] [hb:h21] token containing Unicode characters is transmitted verbatim', async () => {
    const unicodeToken = 'tok-中文-élève-сессия'
    const allHeaderValues: string[] = []
    const fetchFn = vi.fn((_url: unknown, init?: RequestInit) => {
      const h = new Headers(init?.headers as HeadersInit | undefined)
      h.forEach((v) => allHeaderValues.push(v))
      return Promise.resolve(jsonRes(200, { data: {} }))
    }) as unknown as typeof fetch
    await makeClient(fetchFn, () => ({ token: unicodeToken })).request('/api/x')
    expect(allHeaderValues.join(' ')).toContain(unicodeToken)
  })

  it('[adversarial] [hb:h26] token with embedded newline is not silently split at the newline boundary', async () => {
    const tokenWithNewline = 'part1\npart2'
    const allHeaderValues: string[] = []
    const fetchFn = vi.fn((_url: unknown, init?: RequestInit) => {
      const h = new Headers(init?.headers as HeadersInit | undefined)
      h.forEach((v) => allHeaderValues.push(v))
      return Promise.resolve(jsonRes(200, { data: {} }))
    }) as unknown as typeof fetch
    await makeClient(fetchFn, () => ({ token: tokenWithNewline })).request('/api/x')
    const combined = allHeaderValues.join('')
    expect(combined).toContain('part1')
    expect(combined).toContain('part2')
  })

  it('[adversarial] [hb:h27] session present on first call then absent on second — second carries no credential', async () => {
    const capturedAuths: Array<string | null> = []
    const fetchFn = vi.fn((_url: unknown, init?: RequestInit) => {
      const h = new Headers(init?.headers as HeadersInit | undefined)
      capturedAuths.push(h.get('authorization'))
      return Promise.resolve(jsonRes(200, { data: {} }))
    }) as unknown as typeof fetch

    let currentSession: Session = { token: 'tok-abc' }
    const client = makeClient(fetchFn, () => currentSession)

    await client.request('/api/x')
    currentSession = null
    await client.request('/api/x')

    expect(capturedAuths[0]).not.toBeNull()
    expect(capturedAuths[1]).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// B1 — Navigation: unknown and malformed paths
// ---------------------------------------------------------------------------

describe('routes adversarial — unknown paths', () => {
  it('[adversarial] [hb:h19] deeply nested unknown path renders a not-found screen with a way back', async () => {
    routeRender('/a/b/c/d/e/f/g/h')
    expect(document.body.textContent?.length).toBeGreaterThan(0)
    const backElement =
      screen.queryByRole('link', { name: /dashboard|home/i }) ??
      screen.queryByRole('button', { name: /dashboard|home/i })
    expect(backElement).not.toBeNull()
  })

  it('[adversarial] [hb:h20] URL-encoded path that matches no route renders not-found gracefully', async () => {
    routeRender('/unknown%2Fpath%2Fhere')
    expect(document.body.textContent?.length).toBeGreaterThan(0)
  })

  it('[adversarial] [hb:h28] path containing a null byte renders not-found without crashing', async () => {
    routeRender('/some\x00path')
    expect(document.body.textContent?.length).toBeGreaterThan(0)
  })

  it('[adversarial] [hb:h29] double-slash prefix path renders not-found without crashing', async () => {
    routeRender('//double-slash-prefix')
    expect(document.body.textContent?.length).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// B4 — Config: getConfig smoke
// ---------------------------------------------------------------------------

describe('getConfig adversarial', () => {
  it('[adversarial] [hb:h23] getConfig returns a non-null object without throwing', () => {
    expect(() => getConfig()).not.toThrow()
    const cfg = getConfig()
    expect(cfg).not.toBeNull()
    expect(typeof cfg).toBe('object')
  })

  it('[adversarial] [hb:h30] getConfig is stable across multiple calls — same shape each time', () => {
    const a = getConfig()
    const b = getConfig()
    expect(typeof a).toBe('object')
    expect(JSON.stringify(a)).toBe(JSON.stringify(b))
  })
})
```

---

### `.dark-factory/holdouts/frontend-shell/holdout-bridge.json`

```json
[
  { "test_id": "h01", "spec_anchor": "B2" },
  { "test_id": "h02", "spec_anchor": "B2" },
  { "test_id": "h03", "spec_anchor": "B2" },
  { "test_id": "h04", "spec_anchor": "B2" },
  { "test_id": "h05", "spec_anchor": "B2" },
  { "test_id": "h06", "spec_anchor": "B2" },
  { "test_id": "h07", "spec_anchor": "B3" },
  { "test_id": "h08", "spec_anchor": "B3" },
  { "test_id": "h09", "spec_anchor": "B3" },
  { "test_id": "h10", "spec_anchor": "B3" },
  { "test_id": "h11", "spec_anchor": "B3" },
  { "test_id": "h12", "spec_anchor": "B3" },
  { "test_id": "h13", "spec_anchor": "B2" },
  { "test_id": "h14", "spec_anchor": "B2" },
  { "test_id": "h15", "spec_anchor": "B2" },
  { "test_id": "h16", "spec_anchor": "B2" },
  { "test_id": "h17", "spec_anchor": "B2" },
  { "test_id": "h18", "spec_anchor": "B2" },
  { "test_id": "h19", "spec_anchor": "B1" },
  { "test_id": "h20", "spec_anchor": "B1" },
  { "test_id": "h21", "spec_anchor": "B3" },
  { "test_id": "h22", "spec_anchor": "B2" },
  { "test_id": "h23", "spec_anchor": "B4" },
  { "test_id": "h24", "spec_anchor": "B2" },
  { "test_id": "h25", "spec_anchor": "B2" },
  { "test_id": "h26", "spec_anchor": "B3" },
  { "test_id": "h27", "spec_anchor": "B3" },
  { "test_id": "h28", "spec_anchor": "B1" },
  { "test_id": "h29", "spec_anchor": "B1" },
  { "test_id": "h30", "spec_anchor": "B4" }
]
```

---

**30 adversarial tests across 4 spec anchors.** Grouped by concern:

| Group | IDs | Probes |
|---|---|---|
| B2 envelope / error surfacing | h01–h06, h13–h18, h22, h24–h25 | Non-JSON body on 200; error-keyed 200; ambiguous envelope; empty body; AbortError; generic throw; incomplete error object; 4xx non-JSON; 204 No Content parse safety; 201 as success; 422 status on RejectionError; type exclusivity; null error key; array/null body |
| B3 credential attachment | h07–h12, h21, h26–h27 | null/undefined/empty-string/whitespace-only session; getSession throwing; colon token; Unicode token; newline-embedded token; session present→absent across two calls |
| B1 navigation | h19–h20, h28–h29 | 8-level deep unknown path; URL-encoded no-match; null-byte path; double-slash prefix |
| B4 config | h23, h30 | getConfig doesn't throw; stable across calls |

**No differential pairs** — the spec has no "MUST NOT normalize/strip/coerce" clause paired with an explicit rejection AC, so none qualify. All 30 anchors use the spec's own `B1`–`B4` tokens, which will resolve as binary by the pipeline (non-`FR-N`/`AC-N` anchors). The `[hb:]` IDs are opaque and contain no intent encoding.
