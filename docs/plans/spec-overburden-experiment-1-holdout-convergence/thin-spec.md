# Frontend Application Shell

## Problem
No frontend exists. We need an application shell — screen navigation, authenticated
backend communication, and safe handling of config/secrets — so feature teams can build
UI on top of it. Stack: React + Vite + TypeScript in the apps/web/ workspace.

## Behavior

B1 — Navigation.
- Visiting the root shows the dashboard.
- There is a login screen.
- An unrecognized path shows a "not found" screen offering a way back to the dashboard.

B2 — Backend communication.
- The app makes backend requests and receives the response data on success.
- A request the backend REJECTS and a request that FAILS TO REACH the backend are both
  surfaced to the caller as errors — never silently treated as success. A rejection carries
  the backend's error identity (code/message/status); a connectivity failure is
  distinguishable from a rejection.
- The success/error envelope is the backend's contract (success: { "data": T };
  error: { "error": { "code", "message" } }).

B3 — Authentication.
- When a user session is present, backend requests are authenticated as that user.
- When no session is present, requests carry no credentials, and none are fabricated.

B4 — Secret safety.
- The code shipped to the browser contains no server-only secret value.
- The app's backend address is supplied by build-time configuration.

## Out of scope
Visual styling, developer-tooling ergonomics, the build toolchain itself.
