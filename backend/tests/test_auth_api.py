"""End-to-end tests for the authentication endpoints."""

from __future__ import annotations

from app.core.security import create_access_token

SIGNUP = {"email": "grace@example.com", "password": "super-secret-1"}


async def test_signup_returns_a_token_and_the_user(client):
    response = await client.post("/api/auth/signup", json=SIGNUP)

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "grace@example.com"
    assert "password" not in str(body)


async def test_signup_rejects_a_duplicate_email_case_insensitively(client):
    await client.post("/api/auth/signup", json=SIGNUP)
    response = await client.post(
        "/api/auth/signup", json={**SIGNUP, "email": "GRACE@example.com"}
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_signup_rejects_a_short_password(client):
    response = await client.post(
        "/api/auth/signup", json={"email": "x@example.com", "password": "short"}
    )
    assert response.status_code == 422


async def test_signup_rejects_a_malformed_email(client):
    response = await client.post(
        "/api/auth/signup", json={"email": "not-an-email", "password": "long-enough-1"}
    )
    assert response.status_code == 422


async def test_login_succeeds_with_the_right_password(client):
    await client.post("/api/auth/signup", json=SIGNUP)
    response = await client.post("/api/auth/login", json=SIGNUP)

    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_failure_does_not_reveal_whether_the_account_exists(client):
    await client.post("/api/auth/signup", json=SIGNUP)

    wrong_password = await client.post(
        "/api/auth/login", json={**SIGNUP, "password": "wrong-password"}
    )
    unknown_user = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever1"}
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


async def test_me_requires_a_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_returns_the_authenticated_user(auth_client):
    response = await auth_client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


async def test_a_garbage_token_is_rejected(client):
    client.headers["Authorization"] = "Bearer not-a-real-jwt"
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_an_expired_token_is_rejected(client):
    client.headers["Authorization"] = (
        f"Bearer {create_access_token('some-user-id', expires_minutes=-5)}"
    )
    response = await client.get("/api/auth/me")

    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


async def test_a_valid_token_for_a_deleted_user_is_rejected(client):
    client.headers["Authorization"] = f"Bearer {create_access_token('ghost-user-id')}"
    assert (await client.get("/api/auth/me")).status_code == 401
