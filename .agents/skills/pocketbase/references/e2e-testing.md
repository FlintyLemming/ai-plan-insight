# E2E Testing for PocketBase Access Control

End-to-end tests verify that API rules work correctly by making HTTP requests as different users and asserting on status codes. The `pb_e2e_helpers` module provides reusable infrastructure; you write the project-specific test logic.

## Quick Start

```python
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "<absolute path to this skill's scripts/ directory>")

from pb_e2e_helpers import TestRunner, req, user_login, create_test_user, pre_cleanup, superuser_delete

t = TestRunner("My App — E2E Tests")

pre_cleanup(["test@example.com"])
user_id = create_test_user("test@example.com", "testpass123!", "Test User")
token, _ = user_login("test@example.com", "testpass123!")

t.section("Posts access")
status, data = req("POST", "/api/collections/posts/records", {"title": "Hello"}, token=token)
t.check("authenticated user can create post", status in (200, 201), f"status={status}")

superuser_delete("users", user_id)
sys.exit(t.summary())
```

## Import Path Setup

Tests must add the skill's `scripts/` directory to `sys.path` before importing. The absolute path depends on where the skill is installed (e.g., `~/.claude/skills/pocketbase/scripts` or `<project>/.claude/skills/pocketbase/scripts`). Resolve the correct path at code-generation time:

```python
import sys, os
sys.path.insert(0, "<absolute path to this skill's scripts/ directory>")
from pb_e2e_helpers import TestRunner, req, user_login, create_test_user, pre_cleanup, superuser_delete
```

## Test Structure Pattern

Every E2E test follows this structure:

```python
def main():
    t = TestRunner("Project Name — E2E Tests")
    user_ids = []  # track for cleanup

    try:
        # 1. Pre-cleanup: remove stale data from previous runs
        pre_cleanup(["alice@test.example", "bob@test.example"])

        # 2. Setup: create test users
        alice_id = create_test_user("alice@test.example", "alicepass123!", "Alice")
        user_ids.append(alice_id)
        alice_token, _ = user_login("alice@test.example", "alicepass123!")

        # 3. Test sections
        t.section("1. Collection access")
        status, data = req("POST", "/api/collections/posts/records",
                          {"title": "Test"}, token=alice_token)
        t.check("alice can create post", status in (200, 201), f"status={status}")

    finally:
        # 4. Cleanup: delete test data (always runs)
        for uid in user_ids:
            superuser_delete("users", uid)

    sys.exit(t.summary())
```

Key points:
- `try/finally` ensures cleanup runs even when tests fail
- `pre_cleanup()` makes tests idempotent (safe to re-run)
- Track created IDs in variables for cleanup
- Call `sys.exit(t.summary())` to set the process exit code
- **Initialize ALL cleanup variables** (e.g., `user_id = None`, `record_id = None`) **before** the `try` block — otherwise a `NameError` in `finally` will mask the real error

## Expected HTTP Status Codes

When an API rule **denies** access, PocketBase returns different status codes depending on the rule type:

| Rule | Denial behavior |
|------|----------------|
| `listRule` | HTTP 200 with empty `items` array (NOT 403) |
| `viewRule` | HTTP 404 Not Found |
| `createRule` | HTTP 400 Bad Request |
| `updateRule` | HTTP 404 Not Found |
| `deleteRule` | HTTP 404 Not Found |
| Rule is `null` (locked) | HTTP 403 Forbidden |

When access is **granted**:

| Operation | Success status |
|-----------|---------------|
| Create | 200 or 201 |
| Read (list/view) | 200 |
| Update | 200 |
| Delete | 204 |

**Important:** `listRule` denial looks like "no records found" — you cannot distinguish it from an empty collection via HTTP status alone. Always test with known data.

## Multi-User Test Pattern

For access-control testing, use three personas that cover all permission boundaries:

| Persona | Role | Purpose |
|---------|------|---------|
| **alice** | Owner / creator | Tests positive access for the resource owner |
| **bob** | Member / collaborator | Tests positive access for authorized non-owners |
| **charlie** | Outsider | Tests negative access (should be denied) |

```python
TEST_EMAILS = ["alice@test.example", "bob@test.example", "charlie@test.example"]

pre_cleanup(TEST_EMAILS)

alice_id   = create_test_user("alice@test.example",   "alicepass123!",   "Alice")
bob_id     = create_test_user("bob@test.example",     "bobpass123!",     "Bob")
charlie_id = create_test_user("charlie@test.example", "charliepass123!", "Charlie")

alice_token,   _ = user_login("alice@test.example",   "alicepass123!")
bob_token,     _ = user_login("bob@test.example",     "bobpass123!")
charlie_token, _ = user_login("charlie@test.example", "charliepass123!")
```

## Common Test Scenarios

### Owner-Only Access

```python
# createRule: @request.auth.id != ""
# viewRule/updateRule/deleteRule: owner = @request.auth.id

# Owner can CRUD
status, data = req("POST", "/api/collections/posts/records",
                  {"title": "My Post", "owner": alice_id}, token=alice_token)
t.check("owner can create", status in (200, 201))
post_id = data["id"]

status, _ = req("GET", f"/api/collections/posts/records/{post_id}", token=alice_token)
t.check("owner can view", status == 200)

# Non-owner denied
status, _ = req("GET", f"/api/collections/posts/records/{post_id}", token=bob_token)
t.check("non-owner cannot view (404)", status == 404)
```

### Membership-Based Access (cross-collection `?=` rules)

```python
# viewRule: @request.auth.id ?= @collection.members.user
# This pattern uses a junction/membership collection

# Member can view
status, _ = req("GET", f"/api/collections/projects/records/{project_id}", token=bob_token)
t.check("member can view project", status == 200)

# Non-member denied
status, _ = req("GET", f"/api/collections/projects/records/{project_id}", token=charlie_token)
t.check("non-member cannot view project (404)", status == 404)

# List denial returns 200 with empty items
status, data = req("GET", "/api/collections/projects/records", token=charlie_token)
t.check("non-member list is empty", status == 200 and data.get("totalItems") == 0)
```

### Impersonation Prevention

```python
# createRule includes: @request.body.author = @request.auth.id

# Valid: author matches auth
status, _ = req("POST", "/api/collections/comments/records",
               {"content": "Hello", "task": task_id, "author": alice_id},
               token=alice_token)
t.check("alice can comment as herself", status in (200, 201))

# Invalid: author does NOT match auth
status, _ = req("POST", "/api/collections/comments/records",
               {"content": "Fake", "task": task_id, "author": bob_id},
               token=alice_token)
t.check("alice cannot comment as bob (400)", status == 400)
```

### Cascade Delete Verification

```python
from pb_e2e_helpers import superuser_get

# Delete parent record
status, _ = req("DELETE", f"/api/collections/projects/records/{project_id}",
               token=alice_token)
t.check("owner can delete project", status == 204)

# Verify children are cascade-deleted (use superuser to bypass rules)
st, _ = superuser_get("tasks", task_id)
t.check("task is cascade-deleted", st == 404)

st, _ = superuser_get("project_members", member_id)
t.check("member record is cascade-deleted", st == 404)
```

### Role-Based Access

```python
# updateRule: @request.auth.id ?= @collection.members.user && @collection.members.role = "admin"

status, _ = req("PATCH", f"/api/collections/projects/records/{project_id}",
               {"name": "Updated"}, token=admin_token)
t.check("admin member can update", status == 200)

status, _ = req("PATCH", f"/api/collections/projects/records/{project_id}",
               {"name": "Hacked"}, token=regular_token)
t.check("regular member cannot update (404)", status == 404)
```

## Cleanup Best Practices

1. **`try/finally`** — Always wrap tests so cleanup runs even on failure
2. **`pre_cleanup()`** — Delete stale test users at the start for idempotent re-runs
3. **Cascade delete simplification** — If deleting a parent cascades to children, you only need to delete the parent (and the test users)
4. **Track IDs** — Store created record IDs in variables, set to `None` after deletion to avoid double-delete

```python
project_id = None
try:
    # ... create project, run tests ...
    project_id = data["id"]

    # If test deletes the project itself (e.g., cascade test):
    status, _ = req("DELETE", f"/api/collections/projects/records/{project_id}", token=token)
    if status == 204:
        project_id = None  # already deleted — skip cleanup
finally:
    if project_id:
        superuser_delete("projects", project_id)
    for uid in user_ids:
        superuser_delete("users", uid)
```

## Helper API Reference

| Function | Returns | Description |
|----------|---------|-------------|
| `TestRunner(title)` | instance | Initialize test suite, prints header |
| `t.ok(label)` | — | Record passing test |
| `t.fail(label, detail)` | — | Record failing test |
| `t.check(label, condition, detail)` | — | Assert condition |
| `t.section(title)` | — | Print section header |
| `t.summary()` | `0` or `1` | Print results, return exit code |
| `req(method, path, data, token)` | `(status, dict)` | HTTP request as user |
| `user_login(email, password, collection)` | `(token, user_id)` | Authenticate user |
| `create_test_user(email, password, name, collection)` | `user_id` | Create via public API |
| `superuser_create_user(email, password, name, collection)` | `user_id` | Create via superuser |
| `pre_cleanup(emails, collection)` | — | Delete stale test users |
| `superuser_delete(collection, record_id)` | — | Delete record as superuser |
| `superuser_get(collection, record_id)` | `(status, dict)` | GET record as superuser |
| `superuser_list(collection, filter_expr)` | `(status, dict)` | List records as superuser |

## Complete Example

Full working test for a blog with owner-only access:

```python
#!/usr/bin/env python3
"""
E2E test for blog posts with owner-only access control.

Collections:
  - users (auth): default auth collection
  - posts (base): title, content, author (relation → users)
    - listRule:   @request.auth.id != ""
    - viewRule:   author = @request.auth.id
    - createRule: @request.auth.id != "" && @request.body.author = @request.auth.id
    - updateRule: author = @request.auth.id
    - deleteRule: author = @request.auth.id
"""
import sys, os

sys.path.insert(0, "<absolute path to this skill's scripts/ directory>")

from pb_e2e_helpers import (
    TestRunner, req, user_login, create_test_user,
    pre_cleanup, superuser_delete,
)

TEST_EMAILS = ["alice@test.example", "bob@test.example"]


def main():
    t = TestRunner("Blog — E2E Access Control Tests")
    # IMPORTANT: Initialize ALL cleanup variables BEFORE the try block.
    # If creation fails and these aren't defined, the finally block
    # raises NameError, masking the real error.
    alice_id = bob_id = post_id = None

    try:
        pre_cleanup(TEST_EMAILS)

        t.section("Setup: create test users")
        alice_id = create_test_user("alice@test.example", "alicepass123!", "Alice")
        bob_id   = create_test_user("bob@test.example",   "bobpass123!",   "Bob")
        alice_token, _ = user_login("alice@test.example", "alicepass123!")
        bob_token,   _ = user_login("bob@test.example",   "bobpass123!")
        t.ok("users created and logged in")

        # -- Create --
        t.section("1. Post creation")
        status, data = req("POST", "/api/collections/posts/records",
                          {"title": "Alice's Post", "content": "Hello", "author": alice_id},
                          token=alice_token)
        t.check("alice can create post", status in (200, 201), f"status={status}")
        post_id = data.get("id")

        status, _ = req("POST", "/api/collections/posts/records",
                        {"title": "Impersonate", "content": "Fake", "author": alice_id},
                        token=bob_token)
        t.check("bob cannot create post as alice (400)", status == 400, f"status={status}")

        # -- Read --
        t.section("2. Post visibility")
        status, _ = req("GET", f"/api/collections/posts/records/{post_id}", token=alice_token)
        t.check("alice can view own post", status == 200, f"status={status}")

        status, _ = req("GET", f"/api/collections/posts/records/{post_id}", token=bob_token)
        t.check("bob cannot view alice's post (404)", status == 404, f"status={status}")

        status, data = req("GET", "/api/collections/posts/records", token=bob_token)
        t.check("bob's list is empty (listRule: auth only, viewRule: owner)",
                status == 200 and data.get("totalItems") == 0,
                f"status={status} totalItems={data.get('totalItems')}")

        # -- Update --
        t.section("3. Post update")
        status, _ = req("PATCH", f"/api/collections/posts/records/{post_id}",
                        {"title": "Updated"}, token=alice_token)
        t.check("alice can update own post", status == 200, f"status={status}")

        status, _ = req("PATCH", f"/api/collections/posts/records/{post_id}",
                        {"title": "Hacked"}, token=bob_token)
        t.check("bob cannot update alice's post (404)", status == 404, f"status={status}")

        # -- Delete --
        t.section("4. Post deletion")
        status, _ = req("DELETE", f"/api/collections/posts/records/{post_id}", token=bob_token)
        t.check("bob cannot delete alice's post (404)", status == 404, f"status={status}")

        status, _ = req("DELETE", f"/api/collections/posts/records/{post_id}", token=alice_token)
        t.check("alice can delete own post (204)", status == 204, f"status={status}")
        if status == 204:
            post_id = None

    finally:
        if post_id:
            superuser_delete("posts", post_id)
        for uid in [alice_id, bob_id]:
            if uid:
                superuser_delete("users", uid)

    sys.exit(t.summary())


if __name__ == "__main__":
    main()
```
