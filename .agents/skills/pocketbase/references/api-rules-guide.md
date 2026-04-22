# API Rules Design Guide

Comprehensive guide for designing PocketBase API rules — security patterns, operators, macros, and common pitfalls.

> **Common pitfalls:** Read `references/gotchas.md` — especially the `""` vs `null` trap.

---

## Rule Fundamentals

API rules are **filter expressions** evaluated per-request against the target record:

- If the expression **matches** → access **GRANTED**
- If the expression **does not match** → access **DENIED**
- Rules are evaluated in the context of the requesting user and the specific record being accessed

Rules are set per operation (list, view, create, update, delete) on each collection. Auth collections have two additional rules: `authRule` and `manageRule`.

---

## Rule States Quick Reference

| Rule Value | Who Can Access | Notes |
|------------|---------------|-------|
| `null` | Superusers only | Default for new collections. Most restrictive. |
| `""` | Anyone (guests + users) | Fully public. Do not use unless intentional. |
| `"@request.auth.id != ''"` | Authenticated users | Requires valid auth token |
| `"@request.auth.id = id"` | Record owner | `id` refers to the record's own `id` field |

### HTTP Response on Denial by Rule Type

| Rule | Denied Response |
|------|----------------|
| `listRule` | HTTP 200 with empty `items` array (NOT 403) |
| `viewRule` | HTTP 404 Not Found |
| `updateRule` | HTTP 404 Not Found |
| `deleteRule` | HTTP 404 Not Found |
| `createRule` | HTTP 400 Bad Request |
| Rule is `null` (locked) | HTTP 403 Forbidden |

**Important:** listRule denial looks like "no records found" — you cannot distinguish it from an empty collection via HTTP status alone.

---

## Five Rule Types (+ Auth Extras)

### Base and Auth Collections

| Rule | Applies To | Description |
|------|-----------|-------------|
| `listRule` | `GET /records` | Controls which records appear in list results |
| `viewRule` | `GET /records/:id` | Controls whether a single record can be fetched |
| `createRule` | `POST /records` | Controls whether a new record can be created |
| `updateRule` | `PATCH /records/:id` | Controls whether a record can be updated |
| `deleteRule` | `DELETE /records/:id` | Controls whether a record can be deleted |

### Auth Collections Only

| Rule | Description |
|------|-------------|
| `authRule` | Additional constraint applied during authentication (login). Prevents certain users from logging in even if credentials are correct. |
| `manageRule` | When satisfied, allows full CRUD bypass on OTHER auth records (like a superuser for that collection). Used for admin panels. |

> **Important:** `manageRule` does NOT override `null` rules. If `createRule`, `updateRule`, or `deleteRule` is `null` (locked), `manageRule` cannot bypass them — only superusers can. To let admin users manage other users, set each rule explicitly (e.g., `"@request.auth.role = 'admin' || @request.auth.id = id"`).

---

## Request Macros

Use these in rule expressions to reference the current request context.

### `@request.auth.*`

Fields from the authenticated user's record. Empty/null if unauthenticated.

| Expression | Description |
|-----------|-------------|
| `@request.auth.id` | Auth user's record ID |
| `@request.auth.collectionName` | Auth collection name (e.g., `"users"`) |
| `@request.auth.email` | Auth user's email |
| `@request.auth.role` | Custom `role` field on the auth record |
| `@request.auth.verified` | Whether email is verified |

Examples:
```
@request.auth.id != ""              // any authenticated user
@request.auth.role = "admin"        // admin role check
@request.auth.verified = true       // verified users only
```

### `@request.body.*`

Fields from the submitted request body (available in `createRule` and `updateRule`).

```
@request.body.title != ""           // title was provided and non-empty
@request.body.role = "user"         // role field set to "user" only
@request.body.role:isset = false    // role field NOT submitted (prevent role injection)
```

### `@request.query.*`

URL query parameters.

```
@request.query.token != ""          // token query param exists
```

### `@request.headers.*`

HTTP headers (lowercase, hyphens replaced with underscores).

```
@request.headers.x_api_key = "secret"   // X-Api-Key: secret
```

### `@request.context`

The execution context. Useful for restricting access to specific flows.

| Value | Description |
|-------|-------------|
| `default` | Standard API request |
| `oauth2` | OAuth2 authentication flow |
| `realtime` | SSE realtime subscription |
| `protectedFile` | Protected file download |

```
@request.context = "default"        // only standard requests (not realtime)
```

---

## Field Modifiers

Append these to field names in rule expressions.

### `:isset`

True if the field was **submitted** in the request body (even if submitted as empty).

```
@request.body.role:isset = false    // role was NOT submitted — prevents role setting
@request.body.status:isset = true   // status was explicitly submitted
```

Use `:isset = false` to **protect sensitive fields** from being set during create/update.

### `:changed`

True if the field value **differs** from the stored value (update only).

```
@request.body.email:changed = false     // email was not changed
@request.body.role:changed = false      // role cannot be changed via this rule
```

### `:length`

The number of items in a multi-value field (select, relation, file).

```
tags:length > 0                     // at least one tag
tags:length <= 5                    // no more than 5 tags
@request.body.members:length <= 10  // submitted members list has ≤ 10 items
```

### `:each`

Apply a condition to **every item** in a multi-value field. All items must satisfy the condition.

```
roles:each != "superadmin"          // none of the roles is "superadmin"
```

### `:lower`

Case-insensitive comparison.

```
@request.auth.username:lower = "admin"   // username "Admin" or "ADMIN" also matches
```

---

## DateTime Macros

Use these instead of hardcoded dates for time-relative rules:

| Macro | Description |
|-------|-------------|
| `@now` | Current UTC datetime |
| `@yesterday` | Yesterday at 00:00:00 UTC |
| `@tomorrow` | Tomorrow at 00:00:00 UTC |
| `@todayStart` | Today at 00:00:00 UTC |
| `@todayEnd` | Today at 23:59:59 UTC |
| `@monthStart` | First day of current month |
| `@monthEnd` | Last day of current month |
| `@yearStart` | January 1 of current year |
| `@yearEnd` | December 31 of current year |

```
publishDate <= @now                 // record is published (publish date has passed)
expiresAt > @now                    // record has not expired
createdAt >= @todayStart            // created today
```

---

## Operators in Rules

### Standard Operators

| Operator | Description |
|----------|-------------|
| `=` | Equal (for multi-value: **ALL** items must equal value) |
| `!=` | Not equal |
| `>` `>=` `<` `<=` | Numeric/date comparison |
| `~` | Contains (LIKE `%value%`) |
| `!~` | Does not contain |

### Any/At-Least-One Operators (`?` prefix)

| Operator | Description |
|----------|-------------|
| `?=` | ANY item equals value |
| `?!=` | ANY item not equal |
| `?>` `?>=` `?<` `?<=` | ANY item comparison |
| `?~` | ANY item contains |
| `?!~` | ANY item not contains |

**For multi-value fields (select, relation, file), almost always use `?=` (any match) instead of `=` (all match).**

> See `references/records-api.md` for the complete filter syntax reference.

---

## Cross-Collection References

Reference records in other collections within a rule using `@collection`:

```
@collection.memberships.userId ?= @request.auth.id
```

This checks if **any record** in `memberships` has `userId` equal to the requesting user's ID. Always use `?=` (not `=`) for `@collection` references — `=` requires ALL rows to match and silently breaks access when a user has 2+ rows in the joined collection. See `references/gotchas.md` → "`@collection` Cross-Collection References Always Require `?=`".

### Syntax

```
@collection.COLLECTION_NAME.FIELD_NAME
```

### Aliased Collections (for multiple joins)

When you need to join the same collection multiple times:

```
@collection.roles:roleA.userId ?= @request.auth.id && @collection.roles:roleA.name ?= "editor"
```

The `:alias` suffix creates a scoped reference to avoid ambiguity.

### Example: Team Membership Check

```
// User can access record only if they're a member of the record's team
@collection.memberships.teamId ?= team && @collection.memberships.userId ?= @request.auth.id
```

---

## Common Security Patterns

### 1. Public Read, Authenticated Write

Suitable for: public content (blog posts, product catalog).

```json
{
  "listRule": "",
  "viewRule": "",
  "createRule": "@request.auth.id != ''",
  "updateRule": "@request.auth.id != ''",
  "deleteRule": "@request.auth.id != ''"
}
```

### 2. Own Records Only (Full CRUD)

User can only see and modify their own records. Field `user` holds the owner ID.

```json
{
  "listRule": "@request.auth.id = user",
  "viewRule": "@request.auth.id = user",
  "createRule": "@request.auth.id != '' && @request.body.user = @request.auth.id",
  "updateRule": "@request.auth.id = user",
  "deleteRule": "@request.auth.id = user"
}
```

**`createRule` note:** Force the `user` field to match the authenticated user, preventing impersonation.

### 3. Creator-Only Edit/Delete, Public Read

Suitable for: comments, reviews, user-generated content.

```json
{
  "listRule": "",
  "viewRule": "",
  "createRule": "@request.auth.id != '' && @request.body.author = @request.auth.id",
  "updateRule": "@request.auth.id = author",
  "deleteRule": "@request.auth.id = author"
}
```

### 4. Role-Based Access (Admin / Editor / Viewer)

Requires a `role` select field on the auth collection with values `admin`, `editor`, `viewer`.

```json
{
  "listRule": "@request.auth.id != ''",
  "viewRule": "@request.auth.id != ''",
  "createRule": "@request.auth.role = 'editor' || @request.auth.role = 'admin'",
  "updateRule": "@request.auth.role = 'editor' || @request.auth.role = 'admin'",
  "deleteRule": "@request.auth.role = 'admin'"
}
```

### 5. Organization-Scoped Access (via Relation Traversal)

Records belong to an organization. Users are members of organizations via `memberships` collection.

```json
{
  "listRule": "@collection.memberships.orgId ?= orgId && @collection.memberships.userId ?= @request.auth.id",
  "viewRule": "@collection.memberships.orgId ?= orgId && @collection.memberships.userId ?= @request.auth.id",
  "createRule": "@collection.memberships.orgId ?= @request.body.orgId && @collection.memberships.userId ?= @request.auth.id",
  "updateRule": "@collection.memberships.orgId ?= orgId && @collection.memberships.userId ?= @request.auth.id",
  "deleteRule": "@collection.memberships.orgId ?= orgId && @collection.memberships.userId ?= @request.auth.id && @collection.memberships.role ?= 'admin'"
}
```

> **`?=` is required for `@collection` references.** Using `=` instead of `?=` causes the rule to silently deny access when a user has 2 or more membership rows. See `references/gotchas.md` → "`@collection` Cross-Collection References Always Require `?=`".

### 6. Field Protection — Prevent Role Escalation

Prevent users from setting or changing sensitive fields (e.g., `role`, `verified`, `plan`).

```json
{
  "createRule": "@request.auth.id != '' && @request.body.role:isset = false",
  "updateRule": "@request.auth.id = id && @request.body.role:isset = false && @request.body.verified:isset = false"
}
```

Combined with role-based create for admin-only field setting:
```json
{
  "createRule": "@request.auth.id != '' && (@request.body.role:isset = false || @request.auth.role = 'admin')"
}
```

### 7. Time-Based Access (Publish Date)

Only show records where publish date has passed. Useful for scheduled content.

```json
{
  "listRule": "publishDate <= @now && publishDate != ''",
  "viewRule": "publishDate <= @now && publishDate != '' || @request.auth.role = 'admin'"
}
```

### 8. Junction Table Bootstrap Problem

**Problem:** A membership `createRule` that requires an existing membership record creates a chicken-and-egg situation — you cannot add the first member because the rule requires membership to pass.

```
// This createRule blocks first-member creation:
createRule: "@collection.memberships.orgId ?= orgId && @collection.memberships.userId ?= @request.auth.id"
```

#### Solution A (Recommended): Auto-Insert via JSVM Hook

Create the first membership automatically when the parent record (e.g., organization) is created. No `createRule` relaxation needed.

```js
// pb_hooks/auto_membership.pb.js
onRecordAfterCreateSuccess((e) => {
  const memberships = $app.findCollectionByNameOrId("memberships")
  const m = new Record(memberships)
  m.set("orgId", e.record.id)
  m.set("userId", e.record.get("ownerId"))  // or @request.auth.id via hook context
  m.set("role", "admin")
  $app.save(m)
  e.next()
}, "organizations")
```

#### Solution B: Allow Owner to Self-Add as First Member

Relax `createRule` to also allow the organization owner:

```json
{
  "createRule": "(@collection.memberships.orgId ?= @request.body.orgId && @collection.memberships.userId ?= @request.auth.id) || (@request.body.orgId:isset = true && @request.body.userId = @request.auth.id)"
}
```

#### Solution C: Superuser API Insert from Application Code

Insert the initial membership from your application backend using a superuser token, bypassing API rules entirely.

---

## Testing Rules

> **WARNING:** Superusers bypass ALL rules. Never test rule behavior while authenticated as a superuser — you will always get access.

### Test Matrix

For each rule change, verify these four cases:

| Scenario | Expected for list | Expected for view/update/delete |
|----------|------------------|--------------------------------|
| Unauthenticated | 200 empty OR 403 | 404 OR 403 |
| Authenticated non-owner | 200 empty OR items | 404 OR record |
| Record owner | 200 with items | 200 with record |
| Admin role user | 200 with items | 200 with record |

### Getting a Regular User Token

```bash
# Authenticate as a regular user to test rules
python scripts/pb_auth.py \
  --collection users \
  --identity user@example.com \
  --password secret123
```

Use the returned token in subsequent requests with `Authorization: Bearer TOKEN`.

### Checking listRule Behavior

listRule denial returns HTTP 200 with empty items — **not 403**:

```bash
# Check count as regular user vs superuser
python scripts/pb_records.py list posts
# If items = 0 but you expect records, check the listRule
```

### Verifying createRule Field Protection

Attempt to create a record with a protected field set:

```bash
# Should fail or ignore the role field based on your createRule
python scripts/pb_records.py create users \
  '{"email": "test@example.com", "password": "test1234", "role": "admin"}'
```

If the record is created with `role = "admin"`, your `createRule` is not protecting the field.
