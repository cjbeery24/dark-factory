# AGENTS.md

> This file provides AI agents with project-specific context. It is read by Dark Factory's Implementer and Scenario Architect agents to understand your project's architecture, conventions, and constraints.

## Overview

Brief description of what this project does and its primary purpose.

- **Language:** __LANGUAGE__
- **Framework:** (e.g., Express, FastAPI, Gin, Actix)
- **Architecture:** (e.g., monolith, microservices, serverless)

## Architecture

Describe the high-level architecture. Include:
- Request/response flow
- Key services and their responsibilities
- Data flow between components
- External dependencies (databases, APIs, message queues)

## Directory Structure

```
__SOURCE_DIR__/
├── (describe your directory layout here)
```

## Conventions

### Naming
- Files: (e.g., kebab-case, camelCase, snake_case)
- Functions: (e.g., camelCase, snake_case)
- Types/Classes: (e.g., PascalCase)
- Constants: (e.g., SCREAMING_SNAKE_CASE)

### Error Handling
Describe how errors are handled in this project (e.g., exceptions, Result types, error codes).

### Logging
Describe the logging approach (e.g., structured logging, log levels, logger library).

## Testing

- **Framework:** (e.g., vitest, pytest, go test)
- **Pattern:** (e.g., arrange-act-assert, given-when-then)
- **Location:** `__TEST_DIR__/`
- **Naming:** (e.g., `*.test.ts`, `test_*.py`, `*_test.go`)

## Do / Don't Rules

### Do
- (Project-specific rules agents should follow)

### Don't
- (Project-specific anti-patterns agents should avoid)
