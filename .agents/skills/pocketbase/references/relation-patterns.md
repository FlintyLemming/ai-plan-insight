# Relation Patterns

Design patterns for PocketBase relations — 1:N, N:M, back-relations, expand, and cascadeDelete behavior.

> **Field configuration details:** Read `references/field-types.md` — relation field properties.

---

## Relation Field Basics

A relation field stores the ID(s) of records in another collection.

| Property | Description |
|----------|-------------|
| `collectionId` | Target collection ID (use collection name during creation) |
| `maxSelect` | `1` = single relation (stores string ID), `null` or `>1` = multi (stores array of IDs) |
| `minSelect` | Minimum required relations (0 = optional) |
| `cascadeDelete` | `false` = nullify relation on delete, `true` = cascade delete |

**Important:** The relation field lives on the **"many" side** for 1:N patterns. The "one" side has no field — use back-relations to traverse from that direction.

---

## 1:N Pattern (One-to-Many)

**Example:** One User → Many Posts

Place the relation field on the **"many" side** (Posts), pointing to the "one" side (Users).

```json
// Posts collection — field definition
{
  "name": "author",
  "type": "relation",
  "collectionId": "users",
  "maxSelect": 1,
  "required": true,
  "cascadeDelete": false
}
```

**Access patterns:**

```bash
# Get posts by a specific user (filter from "many" side)
python scripts/pb_records.py list posts \
  --filter 'author = "USER_ID"' --expand "author"

# Expand the author on a single post
python scripts/pb_records.py get posts POST_ID \
  --expand "author"
```

**Back-relation (from "one" side):** To get all posts for a user, expand the back-relation:

```bash
python scripts/pb_records.py get users USER_ID \
  --expand "posts_via_author"
```

---

## N:M Pattern (Many-to-Many)

Two approaches depending on use case.

### Option A: Multi-Select Relation Field

Store multiple IDs in a single relation field. Simple but limited.

```json
// Posts collection — tags as multi-select relation
{
  "name": "tags",
  "type": "relation",
  "collectionId": "tags",
  "maxSelect": null,
  "cascadeDelete": false
}
```

**When to use:** Simple tagging, category assignment, no additional metadata on the relationship.

```bash
# Filter posts with a specific tag
python scripts/pb_records.py list posts \
  --filter 'tags ?= "TAG_ID"' --expand "tags"
```

### Option B: Junction Collection

Create a dedicated collection for the relationship. More flexible.

```json
// post_tags junction collection
{
  "name": "post_tags",
  "type": "base",
  "fields": [
    {
      "name": "post",
      "type": "relation",
      "collectionId": "posts",
      "maxSelect": 1,
      "required": true,
      "cascadeDelete": true
    },
    {
      "name": "tag",
      "type": "relation",
      "collectionId": "tags",
      "maxSelect": 1,
      "required": true,
      "cascadeDelete": true
    },
    // Optional: metadata fields like "sortOrder", "addedAt", "addedBy"
    {
      "name": "sortOrder",
      "type": "number"
    }
  ]
}
```

**When to use:**
- Need metadata on the relationship (order, dates, roles)
- Need to query/filter by relationship attributes
- Need to page through large sets of related records (>1000)
- Need sorted relationships

```bash
# Get tags for a post via junction
python scripts/pb_records.py list post_tags \
  --filter 'post = "POST_ID"' --expand "tag" --sort "sortOrder"
```

> **Bootstrap problem:** If the junction collection's `createRule` requires an existing junction row (e.g., membership check), the very first row cannot be inserted — a chicken-and-egg situation. Use a JSVM hook (`onRecordAfterCreateSuccess`) on the parent collection to auto-insert the first junction row, or relax the `createRule` to allow the owner to self-add. See `references/api-rules-guide.md` → "Junction Table Bootstrap Problem".

---

## Back-Relations

PocketBase automatically exposes reverse traversal via the `{collection}_via_{field}` syntax.

**Naming:** `{relatedCollectionName}_via_{fieldName}`

Examples:
| Relation Field | Back-Relation Expand Name |
|---------------|--------------------------|
| `posts.author` (→ users) | `posts_via_author` |
| `comments.post` (→ posts) | `comments_via_post` |
| `post_tags.post` (→ posts) | `post_tags_via_post` |

**Back-relations always return arrays**, even if only one record exists.

```bash
# Expand all comments for a post (back-relation)
python scripts/pb_records.py get posts POST_ID \
  --expand "comments_via_post"

# Expand nested back-relation (comments with their authors)
python scripts/pb_records.py get posts POST_ID \
  --expand "comments_via_post.author"
```

### Back-Relation Limitations

- Maximum **1000 items** returned in a back-relation expand
- **No sorting** within back-relation expand items
- **No filtering** within back-relation expand items

For large datasets or when you need sorting/filtering, query the related collection directly:

```bash
# Better than expanding back-relation for large datasets
python scripts/pb_records.py list comments \
  --filter 'post = "POST_ID"' \
  --sort "-created" \
  --page 1 --perPage 20
```

---

## Expand Patterns

### Direct Expand

```bash
--expand "author"                   # Expand single relation field
```

### Nested Expand (Dot Notation, up to 6 levels)

```bash
--expand "author.profile"           # Expand author, then author's profile
--expand "post.author.profile"      # Three levels deep
```

### Multiple Expand

```bash
--expand "author,category"          # Expand multiple fields
--expand "author,comments_via_post" # Direct + back-relation
```

### Back-Relation Expand

```bash
--expand "posts_via_author"         # All posts by this user
```

### Combined

```bash
# Expand author + all comments with their authors
--expand "author,comments_via_post.author"
```

**Response structure:** Expanded data appears in the `expand` property nested under the field name.

```json
{
  "id": "POST_ID",
  "title": "Hello",
  "author": "USER_ID",
  "expand": {
    "author": {"id": "USER_ID", "name": "Alice"},
    "comments_via_post": [
      {"id": "C1", "text": "Great!", "expand": {"author": {"id": "U2", "name": "Bob"}}}
    ]
  }
}
```

---

## Relation Traversal in API Rules

Use relation fields for access control checks directly in rules.

### Direct Relation Check

```
// User can access post only if they are the author
@request.auth.id = author
```

### Nested Relation Check

```
// User can access comment if they authored the parent post
@request.auth.id = post.author
```

### Back-Relation in Rules

```
// User can access organization data if they have a membership record
@collection.memberships.userId ?= @request.auth.id && @collection.memberships.orgId ?= id
```

### Cross-Collection Reference

```
// User can view record if they have an active subscription
@collection.subscriptions.userId ?= @request.auth.id && @collection.subscriptions.status ?= "active"
```

> **Always use `?=` (not `=`) for `@collection` references.** Using `=` works with exactly one matching row but silently denies access when 2+ rows exist (e.g., a user with multiple memberships or subscriptions). See `references/gotchas.md` → "`@collection` Cross-Collection References Always Require `?=`".

---

## Relation Modifiers (Append/Remove)

For multi-value relation fields, use `+` and `-` modifiers to avoid replacing all relations:

```bash
# Append a tag (does not replace existing tags)
python scripts/pb_records.py update posts POST_ID \
  '{"tags+": ["NEW_TAG_ID"]}'

# Remove a specific tag
python scripts/pb_records.py update posts POST_ID \
  '{"tags-": ["TAG_ID_TO_REMOVE"]}'

# Replace ALL tags (overwrites existing)
python scripts/pb_records.py update posts POST_ID \
  '{"tags": ["TAG_ID_1", "TAG_ID_2"]}'
```

> **v0.23+ change:** Plain assignment (`"tags": [...]`) now **replaces** all values. Always use `+`/`-` to append/remove individual items.

---

## cascadeDelete Behavior

Controls what happens to the relation field when the **target record** is deleted.

| `cascadeDelete` | On Target Delete | Use When |
|----------------|-----------------|----------|
| `false` (default) | Relation field nullified (set to `""` or `[]`) | Parent can exist without child |
| `true` | Owning record is deleted too | Owning record has no meaning without the target |

**Examples:**

```json
// Post has optional category — set to null if category is deleted
{"name": "category", "type": "relation", "cascadeDelete": false}

// Comment belongs to post — delete comment if post is deleted
{"name": "post", "type": "relation", "cascadeDelete": true}

// Junction record — delete junction if either side is deleted
{"name": "post", "type": "relation", "cascadeDelete": true}
{"name": "tag", "type": "relation", "cascadeDelete": true}
```

**Note:** `cascadeDelete` is **one-directional** — it triggers when the referenced record is deleted, not when the record containing the field is deleted.

**Important:** When `required: true` and `cascadeDelete: false`, deleting the target record is **blocked** (HTTP 400) because it would leave the owning record in an invalid state. Either delete the owning records first, or set `cascadeDelete: true`.

---

## Best Practices

1. **Place relation on the "many" side** — Never put a 1:N relation on the "one" side. Use back-relations to traverse from the "one" side.

2. **Use back-relations for reverse traversal** — Don't duplicate data; rely on `{collection}_via_{field}` expand syntax.

3. **Limit expand depth** — Deep expand chains (>3 levels) are slow. Consider restructuring queries.

4. **Index relation fields** — Relation fields used in filters or rules should be indexed for performance. Enable in field settings.

5. **Use junction collections for large N:M** — If a multi-select relation may exceed 1000 items or needs sorting/metadata, use a junction collection.

6. **Set cascadeDelete deliberately** — Default `false` is safe for most cases. Use `true` only when child records are meaningless without the parent.

7. **Protect relation fields in rules** — Use `@request.body.author:isset = false` or `@request.body.author = @request.auth.id` in `createRule` to prevent clients from assigning records to other users.
