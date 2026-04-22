# Collections API Reference

PocketBase v0.23+ REST API for collection management. All endpoints require superuser authentication.

## Table of Contents

- [List Collections](#list-collections)
- [Get Collection](#get-collection)
- [Create Collection](#create-collection)
- [Update Collection](#update-collection)
- [Delete Collection](#delete-collection)
- [Import Collections](#import-collections)
- [Collection Types](#collection-types)
- [API Rules Syntax](#api-rules-syntax)

---

## List Collections

```
GET /api/collections
```

**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| page | Number | Page number (default: 1) |
| perPage | Number | Items per page (default: 30) |
| sort | String | Sort fields: `@random`, `id`, `created`, `updated`, `name`, `type`, `system`. Prefix `-` for DESC. |
| filter | String | Filter expression. Fields: `id`, `created`, `updated`, `name`, `type`, `system`. |
| fields | String | Comma-separated fields to return. `*` for all. |
| skipTotal | Boolean | Skip total count (returns -1 for totalItems/totalPages). |

**Response (200):**
```json
{
  "page": 1,
  "perPage": 30,
  "totalItems": 2,
  "totalPages": 1,
  "items": [{ "id": "...", "name": "...", "type": "base", "fields": [...] }]
}
```

## Get Collection

```
GET /api/collections/{collectionIdOrName}
```

**Response (200):** Single collection object.

## Create Collection

```
POST /api/collections
```

**Body:**
```json
{
  "name": "posts",
  "type": "base",
  "fields": [
    {"name": "title", "type": "text", "required": true},
    {"name": "content", "type": "editor"}
  ],
  "listRule": "",
  "viewRule": "",
  "createRule": "",
  "updateRule": "",
  "deleteRule": ""
}
```

**Rules:** `null` = superuser only, `""` = anyone, `"@request.auth.id != ''"` = authenticated users.

**Response (200):** Created collection object.

## Update Collection

```
PATCH /api/collections/{collectionIdOrName}
```

Only send the fields you want to change. To add fields, include existing fields + new ones.

**Response (200):** Updated collection object.

## Delete Collection

```
DELETE /api/collections/{collectionIdOrName}
```

**Response (204):** No content.

Cannot delete collections that are referenced by other collections (relation fields).

## Import Collections

```
PUT /api/collections/import
```

**Body:**
```json
{
  "collections": [
    {"name": "collection1", "type": "base", "fields": [...]},
    {"name": "collection2", "type": "auth", "fields": [...]}
  ],
  "deleteMissing": false
}
```

| Param | Description |
|-------|-------------|
| collections | Array of collection objects |
| deleteMissing | If `true`, delete existing collections not in the import (default: `false`) |

**Response (204):** No content.

**Import limitations:**
- **Self-referencing relations** (e.g., `categories.parent → categories`) fail because the collection doesn't exist yet during import. Use a 2-pass strategy: import without the self-ref field, then PATCH to add it.
- **Indexes** must be SQL strings (`"CREATE INDEX ..."`), not JSON objects.
- **Circular cross-references** (A→B, B→A) have the same problem — import one side first, then PATCH the other.

## Collection Types

| Type | Description |
|------|-------------|
| `base` | Standard data collection |
| `auth` | User authentication collection (adds email, password, username, etc.) |
| `view` | Read-only SQL view collection |

**Auth collection** auto-includes system fields: `email`, `emailVisibility`, `verified`, `username`, `password`, `tokenKey`.

**View collection** requires a `viewQuery` field with a SELECT statement.

## API Rules Syntax

Rules control record access. They're filter expressions evaluated per-request.

**Operators:** `=`, `!=`, `>`, `>=`, `<`, `<=`, `~` (contains), `!~` (not contains)

**Grouping:** `(...)`, `&&` (AND), `||` (OR)

**Special variables:**
| Variable | Description |
|----------|-------------|
| `@request.auth.id` | Authenticated user's ID |
| `@request.auth.collectionName` | Auth collection name |
| `@request.auth.*` | Any auth record field |
| `@request.body.*` | Request body fields |
| `@collection.name.*` | Cross-collection reference |

**Examples:**
```
""                                          // Anyone
"@request.auth.id != ''"                    // Any authenticated user
"@request.auth.id = id"                     // Record owner only
"@request.auth.role = 'admin'"              // Users with admin role
"@request.auth.id = user.id || @request.auth.role = 'admin'"  // Owner or admin
```

> **Comprehensive rules guide:** Read `references/api-rules-guide.md`
