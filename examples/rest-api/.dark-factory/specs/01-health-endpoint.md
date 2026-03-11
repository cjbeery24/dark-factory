# Health Endpoint and User CRUD

## Objective

Implement a health check endpoint and basic user CRUD operations.

## Functional Requirements

- **FR-1:** `GET /health` returns `{"status": "ok"}` with HTTP 200
- **FR-2:** `GET /api/users` returns a JSON array of all users
- **FR-3:** `GET /api/users/{id}` returns a single user or HTTP 404
- **FR-4:** `POST /api/users` creates a user with `name` and `email`, returns HTTP 201

## Edge Cases

- **EC-1:** `GET /api/users/{id}` with non-existent ID returns 404 with error detail
- **EC-2:** `GET /api/users` with no users returns an empty array `[]`

## Non-Functional Requirements

- **NFR-1:** Health endpoint responds in < 50ms
- **NFR-2:** All responses use `application/json` content type

## Target Files

- `src/app/main.py`

## Verification

- All functional requirements testable via HTTP client
- Health endpoint is the first thing to verify after deployment
