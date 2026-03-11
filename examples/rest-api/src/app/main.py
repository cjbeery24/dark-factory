"""Minimal REST API — Dark Factory example project."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Dark Factory Example API")

# In-memory store — not thread-safe, fine for this example.
_users: dict[int, dict] = {}
_next_id: int = 1


class CreateUserRequest(BaseModel):
    name: str
    email: str


@app.get("/health")
def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/users")
def list_users() -> list[dict]:
    """List all users."""
    return list(_users.values())


@app.get("/api/users/{user_id}")
def get_user(user_id: int) -> dict:
    """Get a single user by ID."""
    if user_id not in _users:
        raise HTTPException(status_code=404, detail="User not found")
    return _users[user_id]


@app.post("/api/users", status_code=201)
def create_user(body: CreateUserRequest) -> dict:
    """Create a new user."""
    global _next_id
    user = {"id": _next_id, "name": body.name, "email": body.email}
    _users[_next_id] = user
    _next_id += 1
    return user
