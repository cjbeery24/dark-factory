# REST API Example — Project Plan

## Overview

Build a minimal REST API with three specs demonstrating the Dark Factory pipeline.

## Architecture Decisions

- **Framework:** FastAPI (async, OpenAPI docs built-in)
- **Storage:** In-memory dict (no database for simplicity)
- **Auth:** JWT tokens via `python-jose` (spec 3)

## Build Phases

### Phase 1: Core CRUD
- **Spec:** `01-health-endpoint` — Health check + basic user CRUD
- **Dependencies:** None

### Phase 2: Validation
- **Spec:** `02-input-validation` — Request body validation, error responses
- **Dependencies:** Phase 1

### Phase 3: Authentication
- **Spec:** `03-jwt-auth` — JWT-based authentication for protected endpoints
- **Dependencies:** Phase 1, Phase 2
