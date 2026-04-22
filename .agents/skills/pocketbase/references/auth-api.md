# Authentication API Reference

PocketBase v0.23+ authentication endpoints.

## Table of Contents

- [Superuser Authentication](#superuser-authentication)
- [User Authentication (Password)](#user-authentication-password)
- [OAuth2 Authentication](#oauth2-authentication)
- [Impersonate](#impersonate)
- [Refresh Token](#refresh-token)
- [Request Password Reset](#request-password-reset)
- [Confirm Password Reset](#confirm-password-reset)
- [Request Email Verification](#request-email-verification)
- [Confirm Email Verification](#confirm-email-verification)
- [Request Email Change](#request-email-change)
- [Auth Token Format](#auth-token-format)

---

## Superuser Authentication

```
POST /api/collections/_superusers/auth-with-password
```

**Body:**
```json
{
  "identity": "admin@example.com",
  "password": "your-password"
}
```

**Response (200):**
```json
{
  "token": "eyJhbGciOi...",
  "record": {
    "id": "...",
    "email": "admin@example.com",
    "collectionName": "_superusers",
    ...
  }
}
```

The token is used in the `Authorization` header for subsequent requests.

## User Authentication (Password)

```
POST /api/collections/{collectionIdOrName}/auth-with-password
```

Works with any auth-type collection (e.g., `users`).

**Body:**
```json
{
  "identity": "user@example.com",
  "password": "user-password"
}
```

**Optional Body Fields:**
| Field | Description |
|-------|-------------|
| identityField | Specific field to match against (default: auto-detect) |

**Optional Query Params:** `expand`, `fields`

**Response (200):** Same structure as superuser (`token` + `record`).

## OAuth2 Authentication

```
POST /api/collections/{collectionIdOrName}/auth-with-oauth2
```

**Body:**
```json
{
  "provider": "google",
  "code": "AUTH_CODE",
  "codeVerifier": "CODE_VERIFIER",
  "redirectURL": "https://example.com/callback"
}
```

**Optional Body Fields:**
| Field | Description |
|-------|-------------|
| createData | Object with additional fields for new user creation |

**Response (200):**
```json
{
  "token": "...",
  "record": {...},
  "meta": {
    "id": "oauth2-account-id",
    "name": "User Name",
    "username": "username",
    "email": "user@gmail.com",
    "avatarURL": "https://...",
    "accessToken": "provider-access-token",
    "refreshToken": "provider-refresh-token",
    "rawUser": {}
  }
}
```

## Impersonate

```
POST /api/collections/{collectionIdOrName}/impersonate
```

Superuser-only. Generates a non-refreshable auth token for another user.

**Body:**
```json
{
  "userId": "TARGET_USER_ID",
  "duration": 3600
}
```

**Response (200):**
```json
{
  "token": "...",
  "record": {...}
}
```

## Refresh Token

```
POST /api/collections/{collectionIdOrName}/auth-refresh
```

Requires a valid auth token in the Authorization header.

**Response (200):** New `token` + `record`.

## Request Password Reset

```
POST /api/collections/{collectionIdOrName}/request-password-reset
```

**Body:**
```json
{"email": "user@example.com"}
```

**Response (204):** No content. Sends email if the user exists (no error if not found for security).

## Confirm Password Reset

```
POST /api/collections/{collectionIdOrName}/confirm-password-reset
```

**Body:**
```json
{
  "token": "RESET_TOKEN_FROM_EMAIL",
  "password": "new-password",
  "passwordConfirm": "new-password"
}
```

**Response (204):** No content.

## Request Email Verification

```
POST /api/collections/{collectionIdOrName}/request-verification
```

**Body:**
```json
{"email": "user@example.com"}
```

## Confirm Email Verification

```
POST /api/collections/{collectionIdOrName}/confirm-verification
```

**Body:**
```json
{"token": "VERIFICATION_TOKEN_FROM_EMAIL"}
```

## Request Email Change

```
POST /api/collections/{collectionIdOrName}/request-email-change
```

Requires auth token. **Body:**
```json
{"newEmail": "new@example.com"}
```

## Auth Token Format

PocketBase uses JWT tokens. The Authorization header format:

```
Authorization: TOKEN_VALUE
```

Note: PocketBase does **not** use the `Bearer` prefix — just the raw token value.

Token payload contains:
- `id` — Record ID
- `type` — `"authRecord"`
- `collectionId` — Collection ID
- `exp` — Expiration timestamp
