# Digital Twins: LLM-as-Evaluator

## What Are Digital Twins?

In Dark Factory, a "digital twin" is an LLM agent that interacts with a running instance of your application the way a real user or integration would. Instead of testing code with unit tests and assertions, the Evaluator agent makes HTTP requests and validates responses against behavioral scenarios.

This approach catches classes of bugs that unit tests miss:
- **Integration failures** — components that pass unit tests individually but fail when wired together
- **Environment issues** — configuration, networking, and deployment problems
- **Behavioral drift** — the application does something subtly different from what the spec describes
- **Missing endpoints** — routes that exist in code but aren't properly registered

## When to Use Digital Twins

Use digital twins when your project:
1. Exposes an HTTP API (REST, GraphQL, etc.)
2. Can be deployed ephemerally (e.g., via docker-compose)
3. Has behavioral requirements that are best verified through end-to-end interaction

Digital twins are **complementary** to blind scenarios, not a replacement. Blind scenarios test code correctness; digital twins test deployed behavior.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Scenario    │     │   Evaluator  │     │  Deployed App   │
│  Architect   │────▶│   Agent      │────▶│  (ephemeral)    │
│  (.eval.md)  │     │  (Docker)    │     │  (docker-compose│
└─────────────┘     └──────────────┘     └─────────────────┘
      │                    │                      │
      │                    │   HTTP requests       │
      │                    │◀─────────────────────▶│
      │                    │                      │
      ▼                    ▼                      ▼
 .dark-factory/      .dark-factory/         Torn down after
 scenarios/          reports/               evaluation
```

## Setup

### 1. Create an ephemeral deployment

Copy the example template and customize:

```bash
cp dark-factory/templates/docker-compose.test.yml.example docker-compose.test.yml
# Edit to match your stack
```

### 2. Configure deployment in config.sh

```bash
DEPLOY_CMD="docker compose -f docker-compose.test.yml up -d"
DEPLOY_TEARDOWN_CMD="docker compose -f docker-compose.test.yml down -v"
DEPLOY_BASE_URL="http://localhost:3000"
DEPLOY_HEALTH_TIMEOUT="60"
DEPLOY_HEALTH_ENDPOINT="/health"
```

### 3. Run the pipeline

The pipeline automatically:
1. Deploys the app after the quality gate (Step 4.5a)
2. Runs the Evaluator agent against `.eval.md` scenarios (Step 4.5b)
3. Tears down the deployment (Step 4.5c)
4. Includes evaluation results in the review

## Writing .eval.md Scenarios

The Scenario Architect generates `.eval.md` files as Layer 9 when `DEPLOY_CMD` is configured. You can also write them manually.

### Format

```markdown
# Scenario: Create user with valid data

## Request
POST /api/users
Content-Type: application/json
{"name": "Alice", "email": "alice@example.com"}

## Expect
- Status: 201
- Body contains "id" field (non-empty string)
- Body "name" equals "Alice"

## Verify
GET /api/users/{response.id}

## Expect
- Status: 200
- Body "name" equals "Alice"
```

### Key rules
- One scenario per `# Scenario:` block
- `## Request` specifies the HTTP method, path, optional headers, and body
- `## Expect` lists assertions against the response
- `## Verify` (optional) makes a follow-up request, often using data from the first response
- `{response.field}` syntax references fields from the previous response

## Information Barriers

The Evaluator agent has the **same barriers as the Scenario Architect**:
- Cannot read implementation source code (SOURCE_DIRS masked)
- Cannot access `.git/` history
- Runs in a dedicated `df-evaluation` Docker network (bridge, not internal — needs to reach the deployed app)

## Cleanup

Deployment teardown is handled by `cleanup_all()`, which runs:
1. On pipeline completion (SHIP or ESCALATION)
2. On crash/interrupt (via EXIT trap)
3. On each iteration cycle (Step 4.5c)

This ensures no orphaned containers are left running.
