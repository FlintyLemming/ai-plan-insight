# Migrations Reference

PocketBase v0.23+ JavaScript migration files.

## Auto-Migration (Recommended)

PocketBase automatically generates migration files when you change collections via the Admin UI or the API. **You do not need to write migration files for schema changes** — PocketBase writes them for you.

### How It Works

1. Make a schema change (create/update a collection via Admin UI or `pb_collections.py`)
2. PocketBase writes a timestamped `.js` file to `pb_migrations/`
3. Commit the generated file to git
4. On next startup (deploy), PocketBase auto-applies all pending migrations

### When to Write Manual Migrations

Use `pb_create_migration.py` (empty template generator) only for operations the Admin UI cannot produce:

| Use case | Why manual |
|----------|-----------|
| Data transformation | Reformat or copy existing field values |
| Raw SQL | Custom indexes, bulk updates, `ALTER TABLE` |
| Seed data | Insert initial records |
| Complex multi-step schema | Multiple interdependent changes |

`pb_create_migration.py` generates an **empty template** — it does not inspect or copy your existing schema.

---

## Table of Contents

- [File Format](#file-format)
- [File Naming](#file-naming)
- [Create Collection](#create-collection)
- [Add Field to Collection](#add-field-to-collection)
- [Remove Field](#remove-field)
- [Update Collection Rules](#update-collection-rules)
- [Run Raw SQL](#run-raw-sql)
- [Seed Data](#seed-data)
- [Create Auth Collection](#create-auth-collection)
- [Create View Collection](#create-view-collection)
- [Add Index](#add-index)

---

## File Format

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  // UP: runs when applying the migration
}, (app) => {
  // DOWN: runs when reverting the migration
})
```

Place files in `pb_migrations/` directory (PocketBase auto-loads them on start).

## File Naming

```
{unix_timestamp}_{description}.js
```

Example: `1704067200_create_posts_collection.js`

Migrations run in order by timestamp.

## Create Collection

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = new Collection({
    name: "posts",
    type: "base",
    fields: [
      {name: "title", type: "text", required: true},
      {name: "content", type: "editor"},
      {name: "published", type: "bool"},
      {name: "publishDate", type: "date"},
    ],
    listRule: "",
    viewRule: "",
    createRule: "@request.auth.id != ''",
    updateRule: "@request.auth.id != ''",
    deleteRule: "@request.auth.id != ''",
  })
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("posts")
  app.delete(collection)
})
```

## Add Field to Collection

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.fields.add(new Field({
    name: "status",
    type: "select",
    values: ["draft", "published", "archived"],
  }))
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.fields.removeByName("status")
  app.save(collection)
})
```

## Remove Field

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.fields.removeByName("obsoleteField")
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.fields.add(new Field({
    name: "obsoleteField",
    type: "text",
  }))
  app.save(collection)
})
```

## Update Collection Rules

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.listRule = "@request.auth.id != ''"
  collection.viewRule = "@request.auth.id != ''"
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.listRule = ""
  collection.viewRule = ""
  app.save(collection)
})
```

## Run Raw SQL

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  app.db().newQuery("CREATE INDEX idx_posts_title ON posts (title)").execute()
}, (app) => {
  app.db().newQuery("DROP INDEX IF EXISTS idx_posts_title").execute()
})
```

## Seed Data

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = app.findCollectionByNameOrId("categories")
  const categories = ["Technology", "Science", "Art", "Music"]
  for (const name of categories) {
    const record = new Record(collection)
    record.set("name", name)
    app.save(record)
  }
}, (app) => {
  // Optional: delete seeded data
  const records = app.findRecordsByFilter("categories", "name ?= 'Technology' || name ?= 'Science' || name ?= 'Art' || name ?= 'Music'")
  for (const record of records) {
    app.delete(record)
  }
})
```

## Create Auth Collection

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = new Collection({
    name: "customers",
    type: "auth",
    fields: [
      {name: "displayName", type: "text"},
      {name: "avatar", type: "file", maxSelect: 1, maxSize: 5242880},
      {name: "role", type: "select", values: ["user", "premium"]},
    ],
    listRule: "@request.auth.id != ''",
    viewRule: "@request.auth.id != ''",
    createRule: "",
    updateRule: "@request.auth.id = id",
    deleteRule: "@request.auth.id = id",
    authRule: "",
    manageRule: null,
  })
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("customers")
  app.delete(collection)
})
```

## Create View Collection

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = new Collection({
    name: "published_posts",
    type: "view",
    viewQuery: `
      SELECT id, title, content, created
      FROM posts
      WHERE published = true
      ORDER BY created DESC
    `,
    listRule: "",
    viewRule: "",
  })
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("published_posts")
  app.delete(collection)
})
```

## Add Index

```javascript
/// <reference path="../pb_data/types.d.ts" />
migrate((app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.indexes = [
    ...collection.indexes,
    "CREATE INDEX idx_posts_published ON posts (published)",
    "CREATE UNIQUE INDEX idx_posts_slug ON posts (slug)",
  ]
  app.save(collection)
}, (app) => {
  const collection = app.findCollectionByNameOrId("posts")
  collection.indexes = collection.indexes.filter(
    idx => !idx.includes("idx_posts_published") && !idx.includes("idx_posts_slug")
  )
  app.save(collection)
})
```
