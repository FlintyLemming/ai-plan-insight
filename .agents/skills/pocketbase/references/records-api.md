# Records API Reference

PocketBase v0.23+ REST API for record management.

## Table of Contents

- [List Records](#list-records)
- [Get Record](#get-record)
- [Create Record](#create-record)
- [Update Record](#update-record)
- [Delete Record](#delete-record)
- [Batch Operations](#batch-operations)
- [Filter Syntax](#filter-syntax)
- [Sort Syntax](#sort-syntax)
- [Expand Relations](#expand-relations)
- [Field Selection](#field-selection)

---

## List Records

```
GET /api/collections/{collectionIdOrName}/records
```

**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| page | Number | Page number (default: 1) |
| perPage | Number | Items per page (default: 30, max: 500) |
| sort | String | Sort expression |
| filter | String | Filter expression |
| expand | String | Expand relations |
| fields | String | Fields to return |
| skipTotal | Boolean | Skip total count query |

**Response (200):**
```json
{
  "page": 1,
  "perPage": 30,
  "totalItems": 100,
  "totalPages": 4,
  "items": [{"id": "...", "collectionName": "...", ...}]
}
```

## Get Record

```
GET /api/collections/{collectionIdOrName}/records/{recordId}
```

Supports `expand` and `fields` query parameters.

## Create Record

```
POST /api/collections/{collectionIdOrName}/records
```

**JSON Body:** Fields matching the collection schema.
```json
{"title": "Hello", "status": true, "author": "RELATION_ID"}
```

Supports `expand` and `fields` query parameters.

**Auth collection records** require `password` and `passwordConfirm` fields.

## Update Record

```
PATCH /api/collections/{collectionIdOrName}/records/{recordId}
```

Only send the fields to update. Supports `expand` and `fields`.

**Special operations for multi-value fields (relation, select, file):**
- Append: `"field+": ["value"]`
- Remove: `"field-": ["value"]`

## Delete Record

```
DELETE /api/collections/{collectionIdOrName}/records/{recordId}
```

**Response (204):** No content.

## Batch Operations

```
POST /api/batch
```

Execute multiple operations atomically.

**Body:**
```json
{
  "requests": [
    {"method": "POST", "url": "/api/collections/posts/records", "body": {"title": "Post 1"}},
    {"method": "PATCH", "url": "/api/collections/posts/records/RECORD_ID", "body": {"title": "Updated"}},
    {"method": "DELETE", "url": "/api/collections/posts/records/RECORD_ID"}
  ]
}
```

**Response (200):** Array of `{status, body}` for each request.

## Filter Syntax

**Format:** `FIELD OPERATOR VALUE`

**Operators:**
| Operator | Description | Example |
|----------|-------------|---------|
| `=` | Equal | `status = true` |
| `!=` | Not equal | `status != false` |
| `>` | Greater than | `count > 10` |
| `>=` | Greater or equal | `count >= 10` |
| `<` | Less than | `count < 10` |
| `<=` | Less or equal | `count <= 10` |
| `~` | Contains (like) | `title ~ "hello"` |
| `!~` | Not contains | `title !~ "draft"` |
| `?=` | Any equal (multi-value) | `tags ?= "news"` |
| `?!=` | Any not equal | `tags ?!= "spam"` |
| `?>` | Any greater | `scores ?> 90` |
| `?>=` | Any greater or equal | `scores ?>= 90` |
| `?<` | Any less | `scores ?< 10` |
| `?<=` | Any less or equal | `scores ?<= 10` |
| `?~` | Any contains | `tags ?~ "tech"` |
| `?!~` | Any not contains | `tags ?!~ "spam"` |

**Grouping:** `(expr1 && expr2) || expr3`

**Values:**
- Strings: `"value"` or `'value'`
- Numbers: `123`, `3.14`
- Booleans: `true`, `false`
- Null: `null`
- Dates: `"2024-01-01 00:00:00"`

**Examples:**
```
title ~ "hello" && created > "2024-01-01"
status = true || (role = "admin" && verified = true)
tags ?= "featured"
@request.auth.id = user
```

## Sort Syntax

Comma-separated fields. Prefix `-` for DESC, `+` (default) for ASC.

**Special fields:** `@random`, `@rowid`

**Examples:**
```
-created                    // Newest first
-created,title              // Newest first, then by title ASC
@random                     // Random order
+title,-created             // Title ASC, then newest first
```

## Expand Relations

Dot-notation for nested relations (up to 6 levels).

```
expand=author                           // Direct relation
expand=author,category                  // Multiple relations
expand=author.profile                   // Nested relation
expand=comments.author,comments.likes   // Multiple nested
```

Expanded data appears under the `expand` property in the response.

> **Relation design patterns:** Read `references/relation-patterns.md`

## Field Selection

Control which fields appear in the response.

```
fields=id,title,created           // Specific fields
fields=*                          // All fields at current level
fields=*,expand.author.name       // All fields + specific expand field
fields=description:excerpt(200)   // Truncated text
```
