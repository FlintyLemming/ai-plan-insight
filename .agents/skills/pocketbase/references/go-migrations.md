# Go Migrations Reference

PocketBase v0.23+ Go migration files — registration, collection creation, field types, and patterns.

---

## PREFERRED WORKFLOW: Auto-Migration via Python Scripts

**Do NOT hand-write Go migration files for collection schema creation.**

Instead, use `pb_collections.py` to create/update collections via the REST API, and let PocketBase auto-generate the Go migration files:

1. Ensure `migratecmd.MustRegister(app, app.RootCmd, migratecmd.Config{Automigrate: true})` is in `main.go`
2. Start PocketBase: `go run . serve`
3. Create collections: `pb_collections.py create '{...}'` or `pb_collections.py import --file collections.json`
4. PocketBase auto-generates `.go` migration files in `pb_migrations/`
5. Commit the auto-generated files to git

Manual Go migration files (in `migrations/`) are only for: **seed data, data transforms, raw SQL, and superuser creation**.

The sections below document the manual migration file format for these limited use cases.

---

## 1. Auto-Migration

When `migratecmd.MustRegister()` is configured with `Automigrate: true`, PocketBase auto-generates Go migration files in `pb_migrations/` whenever you change a collection via the Admin Dashboard or REST API.

```go
migratecmd.MustRegister(app, app.RootCmd, migratecmd.Config{
    Automigrate: isGoRun, // enable only during development
    // See go-framework.md for the isGoRun detection that works with Go 1.24+.
})
```

The auto-generated files are complete and ready to commit. You only need to write manual migrations for operations the Dashboard cannot produce (data transforms, seed data, raw SQL).

---

## 2. Migration File Structure (for manual migrations)

### File Structure

Every Go migration file follows this pattern:

```go
package migrations

import (
	"github.com/pocketbase/pocketbase/core"
	m "github.com/pocketbase/pocketbase/migrations"
)

func init() {
	m.Register(func(app core.App) error {
		// === UP ===
		return nil
	}, func(app core.App) error {
		// === DOWN ===
		return nil
	})
}
```

### Blank Import Required

`main.go` must import the migrations package (even if unused directly):

```go
import _ "yourmodule/migrations"
```

Without this, migrations are never registered and **no error is reported**.

### File Naming

Same convention as JS: `{unix_timestamp}_{description}.go`

```
1700000000_create_posts.go
1700000001_add_status_field.go
1700000002_seed_categories.go
```

Use the template at `assets/migration-template.go` as a starting point.

---

## 3. Collection Schema in Manual Migrations (reference only)

> **Reminder:** For collection creation, prefer `pb_collections.py` + auto-migration (see PREFERRED WORKFLOW above). The examples below are reference for understanding auto-generated code or for rare cases where manual migration is unavoidable.

### Base Collection

```go
func init() {
	m.Register(func(app core.App) error {
		collection := core.NewBaseCollection("posts")

		collection.ListRule = types.Pointer("@request.auth.id != ''")
		collection.ViewRule = types.Pointer("@request.auth.id != ''")
		collection.CreateRule = types.Pointer("@request.auth.id != ''")
		// UpdateRule and DeleteRule left nil = superuser only

		collection.Fields.Add(
			&core.TextField{
				Name:     "title",
				Required: true,
				Max:      200,
			},
			&core.EditorField{
				Name: "content",
			},
			&core.SelectField{
				Name:      "status",
				Required:  true,
				Values:    []string{"draft", "published", "archived"},
				MaxSelect: 1,
			},
		)

		return app.Save(collection)
	}, func(app core.App) error {
		collection, err := app.FindCollectionByNameOrId("posts")
		if err != nil {
			return err
		}
		return app.Delete(collection)
	})
}
```

### Auth Collection

```go
collection := core.NewAuthCollection("users")

collection.Fields.Add(
	&core.TextField{
		Name:     "displayName",
		Required: true,
		Max:      100,
	},
	&core.FileField{
		Name:      "avatar",
		MaxSelect: 1,
		MaxSize:   5242880,
		MimeTypes: []string{"image/jpeg", "image/png", "image/webp"},
	},
)
```

### View Collection

```go
collection := core.NewViewCollection("post_stats")
collection.ViewQuery = "SELECT id, title, (SELECT COUNT(*) FROM comments WHERE comments.post = posts.id) as comment_count FROM posts"

collection.Fields.Add(
	&core.TextField{Name: "title"},
	&core.NumberField{Name: "comment_count"},
)
```

---

## 4. Rule Setting with `types.Pointer()`

Collection rules are `*string` — you cannot assign a string literal directly.

```go
import "github.com/pocketbase/pocketbase/tools/types"

// nil = superuser only (deny all non-superusers)
collection.ListRule = nil

// empty string = allow everyone (including guests)
collection.ListRule = types.Pointer("")

// auth required
collection.ListRule = types.Pointer("@request.auth.id != ''")

// owner only
collection.ViewRule = types.Pointer("@request.auth.id = id")
```

> **Common mistake:** `collection.ListRule = ""` does NOT compile — use `types.Pointer("")`.

---

## 5. Go Field Type Reference

| PB Type | Go Struct | Key Properties |
|---------|-----------|----------------|
| text | `core.TextField` | Name, Required, Min, Max, Pattern |
| number | `core.NumberField` | Name, Required, Min, Max, OnlyInt |
| bool | `core.BoolField` | Name, Required |
| email | `core.EmailField` | Name, Required |
| url | `core.URLField` | Name, Required, Presentable |
| date | `core.DateField` | Name, Required, Min, Max |
| select | `core.SelectField` | Name, Required, Values, MaxSelect |
| json | `core.JSONField` | Name, Required, MaxSize |
| file | `core.FileField` | Name, Required, MaxSelect, MaxSize, MimeTypes, Thumbs, Protected |
| relation | `core.RelationField` | Name, Required, CollectionId, MaxSelect, CascadeDelete |
| editor | `core.EditorField` | Name, Required, MaxSize |
| autodate | `core.AutodateField` | Name, OnCreate, OnUpdate |
| password | `core.PasswordField` | Name, Required, Min, Max |

### Usage

All fields are added as pointers:

```go
collection.Fields.Add(
	&core.TextField{Name: "title", Required: true, Max: 200},
	&core.NumberField{Name: "price", Min: 0, OnlyInt: false},
	&core.SelectField{Name: "status", Values: []string{"draft", "published"}, MaxSelect: 1},
	&core.RelationField{Name: "author", CollectionId: "users", MaxSelect: 1, Required: true},
	&core.FileField{Name: "cover", MaxSelect: 1, MaxSize: 10485760, MimeTypes: []string{"image/jpeg", "image/png"}},
	&core.BoolField{Name: "featured"},
	&core.DateField{Name: "publishedAt"},
	&core.JSONField{Name: "metadata", MaxSize: 65536},
)
```

> **Note:** For `RelationField.CollectionId`, use the target collection's **name** — PocketBase resolves it during migration.

---

## 6. Indexes

```go
// AddIndex(name, unique, columns, optionalWhereClause)
collection.AddIndex("idx_posts_status", false, "status", "")
collection.AddIndex("idx_posts_title_unique", true, "title", "")
collection.AddIndex("idx_posts_published", false, "status, created", "status = 'published'")
```

---

## 7. Common Manual Migration Patterns

> These patterns are for **manual migrations only** (seed data, data transforms, raw SQL, rule changes). For collection schema creation, use `pb_collections.py` instead.

### Add Field to Existing Collection

```go
m.Register(func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("posts")
	if err != nil {
		return err
	}

	collection.Fields.Add(&core.TextField{
		Name:     "subtitle",
		Required: false,
		Max:      300,
	})

	return app.Save(collection)
}, func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("posts")
	if err != nil {
		return err
	}

	collection.Fields.RemoveByName("subtitle")

	return app.Save(collection)
})
```

### Remove Field

```go
m.Register(func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("posts")
	if err != nil {
		return err
	}

	collection.Fields.RemoveByName("obsoleteField")

	return app.Save(collection)
}, func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("posts")
	if err != nil {
		return err
	}

	collection.Fields.Add(&core.TextField{
		Name: "obsoleteField",
	})

	return app.Save(collection)
})
```

### Update Rules

```go
m.Register(func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("posts")
	if err != nil {
		return err
	}

	collection.ListRule = types.Pointer("@request.auth.id != ''")
	collection.ViewRule = types.Pointer("@request.auth.id != ''")

	return app.Save(collection)
}, func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("posts")
	if err != nil {
		return err
	}

	collection.ListRule = nil
	collection.ViewRule = nil

	return app.Save(collection)
})
```

### Raw SQL

```go
m.Register(func(app core.App) error {
	_, err := app.DB().NewQuery("UPDATE posts SET status = 'archived' WHERE created < '2024-01-01'").Execute()
	return err
}, func(app core.App) error {
	_, err := app.DB().NewQuery("UPDATE posts SET status = 'draft' WHERE created < '2024-01-01'").Execute()
	return err
})
```

### Seed Data

```go
m.Register(func(app core.App) error {
	collection, err := app.FindCollectionByNameOrId("categories")
	if err != nil {
		return err
	}

	seeds := []string{"Technology", "Science", "Art", "Sports"}
	for _, name := range seeds {
		record := core.NewRecord(collection)
		record.Set("name", name)
		if err := app.Save(record); err != nil {
			return err
		}
	}

	return nil
}, func(app core.App) error {
	// optional: remove seed data
	records, err := app.FindRecordsByFilter("categories", "name IN ('Technology','Science','Art','Sports')", "", 0, 0)
	if err != nil {
		return err
	}
	for _, r := range records {
		if err := app.Delete(r); err != nil {
			return err
		}
	}
	return nil
})
```

### Create Superuser

```go
m.Register(func(app core.App) error {
	superusers, err := app.FindCollectionByNameOrId(core.CollectionNameSuperusers)
	if err != nil {
		return err
	}

	record := core.NewRecord(superusers)
	record.Set("email", "admin@example.com")
	record.SetPassword("securepassword123")

	return app.Save(record)
}, func(app core.App) error {
	record, err := app.FindAuthRecordByEmail(core.CollectionNameSuperusers, "admin@example.com")
	if err != nil {
		return err
	}
	return app.Delete(record)
})
```
