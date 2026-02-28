# Project Plan: [Project Name]

**Author:** [name]
**Date:** [YYYY-MM-DD]
**Status:** DRAFT | VALIDATED | IN PROGRESS | COMPLETE

---

## 1. Project Overview

**Project Name:** [Short, descriptive name]

**Description:**
[1-3 sentences describing what this project does and why it exists]

**Target Users:**
[Who will use this? Be specific: "Backend engineers on the payments team", not "developers"]

**Business Justification:**
[Why build this now? What happens if we don't?]

**Success Metrics:**
| Metric | Target | How to Measure |
|--------|--------|----------------|
| [e.g., API response time] | [e.g., p95 < 200ms] | [e.g., Datadog dashboard] |
| [e.g., Adoption rate] | [e.g., 80% of team within 2 weeks] | [e.g., Usage analytics] |
| [e.g., Error reduction] | [e.g., 50% fewer support tickets] | [e.g., Jira ticket count] |

---

## 2. Tech Stack

| Category | Choice | Rationale |
|----------|--------|-----------|
| Language | [e.g., TypeScript 5.x] | [Why this language?] |
| Runtime | [e.g., Node.js 20 LTS] | [Why this runtime?] |
| Framework | [e.g., Fastify] | [Why this framework?] |
| Database | [e.g., PostgreSQL 16] | [Why this database?] |
| ORM / Query Builder | [e.g., Drizzle] | [Why this ORM?] |
| Test Framework | [e.g., Vitest] | [Why this test framework?] |
| Linter | [e.g., ESLint + Biome] | [Why these tools?] |
| Formatter | [e.g., Biome] | [Why this formatter?] |
| Package Manager | [e.g., pnpm] | [Why this package manager?] |
| CI/CD | [e.g., GitHub Actions] | [Why this platform?] |

**Key Libraries:**
| Library | Version | Purpose |
|---------|---------|---------|
| [e.g., zod] | [e.g., ^3.22] | [e.g., Runtime validation] |
| [e.g., pino] | [e.g., ^8.0] | [e.g., Structured logging] |

---

## 3. Architecture

**Style:** [e.g., Monolith, Microservices, Serverless, Modular monolith]

**Component Diagram:**
```
[ASCII diagram showing major components and their connections]

Example:
┌─────────┐     ┌──────────┐     ┌──────────┐
│  Client  │────▶│  API GW  │────▶│  Service │
└─────────┘     └──────────┘     └────┬─────┘
                                      │
                                 ┌────▼─────┐
                                 │    DB     │
                                 └──────────┘
```

**Data Flow:**
[Describe how data moves through the system for the primary use case]

1. [Step 1: e.g., Client sends POST /api/resource with JSON body]
2. [Step 2: e.g., API gateway validates auth token]
3. [Step 3: e.g., Service validates input, applies business logic]
4. [Step 4: e.g., Persistence layer writes to database]
5. [Step 5: e.g., Response returned to client]

**External Integrations:**
| Service | Purpose | Protocol | Auth Method |
|---------|---------|----------|-------------|
| [e.g., Stripe] | [e.g., Payment processing] | [e.g., REST] | [e.g., API key] |
| [e.g., SendGrid] | [e.g., Email delivery] | [e.g., REST] | [e.g., API key] |

**Authentication Approach:**
[e.g., JWT with RS256, session-based, OAuth 2.0 + OIDC, API keys, or "N/A — internal tool"]

---

## 4. Data Model

### [Entity Name 1]
| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | [e.g., UUID] | Primary key | [description] |
| [field] | [type] | [constraints] | [description] |
| [field] | [type] | [constraints] | [description] |
| createdAt | [e.g., timestamp] | Not null, default now | [description] |
| updatedAt | [e.g., timestamp] | Not null, default now | [description] |

### [Entity Name 2]
| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | [e.g., UUID] | Primary key | [description] |
| [field] | [type] | [constraints] | [description] |
| [foreignKey] | [type] | FK → [Entity1].id | [description] |

**Relationships:**
- [Entity1] has many [Entity2] (one-to-many via [foreignKey])
- [Entity2] belongs to [Entity1]

**Key Invariants:**
- [e.g., "A user's email must be unique across the system"]
- [e.g., "An order's total must equal the sum of its line items"]
- [e.g., "Soft-deleted records are excluded from all queries by default"]

---

## 5. Directory Structure

```
[project-name]/
├── src/
│   ├── [dir]/         → [description]
│   ├── [dir]/         → [description]
│   ├── [dir]/         → [description]
│   └── [entry file]   → [description]
├── tests/
│   ├── unit/          → [description]
│   └── integration/   → [description]
├── [config files]
└── [other top-level files]
```

**Naming Conventions:**
- Files: [e.g., kebab-case.ts]
- Classes: [e.g., PascalCase]
- Functions: [e.g., camelCase]
- Database tables: [e.g., snake_case]
- Environment variables: [e.g., SCREAMING_SNAKE_CASE]

---

## 6. Non-Functional Requirements

### Performance
| Metric | Target |
|--------|--------|
| [e.g., API response time (p95)] | [e.g., < 200ms] |
| [e.g., Database query time (p99)] | [e.g., < 50ms] |
| [e.g., Startup time] | [e.g., < 3 seconds] |

### Security
- [e.g., All user inputs parameterized in database queries (no string interpolation)]
- [e.g., Passwords hashed with bcrypt, cost factor 12]
- [e.g., CORS restricted to allowed origins]
- [e.g., Rate limiting on all public endpoints]

### Scalability
- [e.g., Must handle 1000 concurrent connections]
- [e.g., Database must support 10M rows in primary table without degradation]

### Accessibility
- [e.g., WCAG 2.1 AA compliance for all UI components]
- [e.g., N/A — backend-only project]

### Observability
- [e.g., Structured JSON logs with request ID correlation]
- [e.g., Health check endpoint at GET /health]
- [e.g., Latency histograms for all endpoints]
- [e.g., Error rate alerting at > 1% over 5-minute window]

---

## 7. Feature Decomposition

| ID | Feature Name | Spec Type | Depends On | Description |
|----|-------------|-----------|------------|-------------|
| F-1 | [e.g., Project scaffolding] | feature | — | [Set up project structure, configs, CI/CD] |
| F-2 | [e.g., Database schema + migrations] | feature | F-1 | [Create tables, indexes, seed data] |
| F-3 | [e.g., User authentication] | feature | F-2 | [Register, login, JWT issuance] |
| F-4 | [e.g., CRUD endpoints for Resource] | api | F-2, F-3 | [Create, read, update, delete Resource] |
| F-5 | [e.g., Input validation layer] | feature | F-1 | [Shared validation schemas with zod] |

**Build Phases** (derived from dependency graph):

**Phase 1** (no dependencies):
- F-1: [Project scaffolding]

**Phase 2** (depends on Phase 1):
- F-2: [Database schema + migrations]
- F-5: [Input validation layer]

**Phase 3** (depends on Phase 2):
- F-3: [User authentication]
- F-4: [CRUD endpoints for Resource]

---

## 8. Shared Patterns

### Error Handling
[e.g., All errors wrapped in AppError class with code, message, statusCode. Unhandled errors caught by global middleware and logged with stack trace.]

### Logging
[e.g., Use pino logger. All logs include requestId, timestamp, level. Sensitive fields (password, token) are redacted.]

### Validation
[e.g., All inputs validated at the boundary using zod schemas. Schemas co-located with route definitions. Validation errors return 400 with field-level details.]

### Testing
[e.g., Unit tests for business logic (no I/O). Integration tests for API endpoints (with test database). Test files co-located with source: `src/foo.ts` → `src/foo.test.ts`.]

### Code Organization
[e.g., Feature-based modules: each feature in `src/features/[name]/` with its own routes, handlers, services, and types. Shared utilities in `src/common/`.]

---

## 9. Out of Scope

The following are explicitly NOT part of this project plan:
- [e.g., Mobile app — will be a separate project]
- [e.g., Admin dashboard — defer to Phase 2]
- [e.g., Multi-tenancy — not needed for MVP]
- [e.g., Internationalization — English only for now]

---

## 10. Open Questions

| ID | Question | Impact | Decision Needed By |
|----|----------|--------|--------------------|
| Q-1 | [e.g., Should we use REST or GraphQL?] | [Affects all API features] | [Before F-4] |
| Q-2 | [e.g., Which cloud provider for hosting?] | [Affects CI/CD and infra features] | [Before F-1] |
