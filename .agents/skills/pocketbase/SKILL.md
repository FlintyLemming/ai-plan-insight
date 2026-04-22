---
name: pocketbase
description: >-
  Skill for operating PocketBase backend via REST API and Go package mode.
  Provides collection CRUD, record CRUD, superuser/user authentication,
  backup & restore, migration file generation (JS and Go), Go hooks,
  custom routes, and design guidance for API rules, relations, and security
  patterns. Use for requests related to PocketBase, pb_migrations,
  collection management, record operations, Go framework embedding, and
  backend design.
license: MIT
metadata:
  version: "1.0.0"
allowed-tools: Read Write Edit Bash Grep Glob
---

# PocketBase Skill

Operate PocketBase backend projects (standalone binary or Go package mode) with bundled Python scripts and references.

## Skill Resources

Use resources in this skill directory first:

- `scripts/` - executable helpers for auth, collections, records, backups, migration template generation
- `references/` - detailed backend docs to load on demand
- `assets/` - migration templates

Prefer `--help` output and this guide first. If behavior is unclear, read only the relevant script file.

## 0. Fast Start

### Mode Detection

1. If `go.mod` contains `github.com/pocketbase/pocketbase` -> Go package mode
2. Otherwise -> standalone mode

### Bootstrap (when PocketBase is not running)

1. Resolve latest version dynamically:

```bash
VERSION=$(curl -s https://api.github.com/repos/pocketbase/pocketbase/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))")
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
OS=$(uname -s | tr A-Z a-z)
curl -sL "https://github.com/pocketbase/pocketbase/releases/download/v${VERSION}/pocketbase_${VERSION}_${OS}_${ARCH}.zip" -o pb.zip
unzip -o pb.zip pocketbase && rm pb.zip
```

2. Create superuser:

```bash
./pocketbase superuser create admin@example.com <password>
```

3. Configure `.env` (and keep `.env` in `.gitignore`):

```env
PB_URL=http://127.0.0.1:8090
PB_SUPERUSER_EMAIL=admin@example.com
PB_SUPERUSER_PASSWORD=your-password
```

4. Start service:

```bash
nohup ./pocketbase serve --http=127.0.0.1:8090 > pb.log 2>&1 &
```

5. Health check:

```bash
python scripts/pb_health.py
```

As of 2026-02-27, latest stable release is `v0.36.5`.

## 1. Design & Safety Checklist

Your model priors may include pre-v0.23 patterns. Validate these before any schema or hook changes.

### v0.23+ Critical Rules

- Use `_superusers` (not `_admins`)
- Use collection `fields` (not `schema`)
- Use flat field properties (no `options` wrapper)
- Use typed field constructors in migrations (`TextField`, `SelectField`, `RelationField`, ...)
- Use `{param}` path syntax in routes (not `:param`)
- JSVM hooks must call `e.next()`; Go hooks must `return e.Next()`
- `@collection.*` filters should use `?=` when multiple rows are possible

### API Rule Defaults

- `null` -> superuser only
- `""` -> public (including unauthenticated)

Default to `null` and open only what is required.

### Go Mode Checks

- Use API/Admin UI for collection changes; let PocketBase auto-generate migrations
- Commit generated files in `pb_migrations/`
- Configure `migratecmd.MustRegister(..., migratecmd.Config{Automigrate: true})` for dev
- Ensure blank import exists when manual migrations are used: `_ "yourmodule/migrations"`
- For rule assignment in manual Go migrations, use `types.Pointer("...")`

## 2. Core Operations

### 2.1 Authentication

Superuser auth:

```bash
python scripts/pb_auth.py
```

User auth:

```bash
python scripts/pb_auth.py --collection users --identity user@example.com --password secret
```

### 2.2 Collections

List / get:

```bash
python scripts/pb_collections.py list
python scripts/pb_collections.py get posts
```

Create / update / delete:

```bash
python scripts/pb_collections.py create --file schema.json
python scripts/pb_collections.py update posts --file updates.json
python scripts/pb_collections.py delete posts
```

Batch import (recommended for multi-collection setup):

```bash
python scripts/pb_collections.py import --file collections.json
```

Important:

- Do not create a new `users` collection; update the existing one
- Use `--file` when payload includes API rules to avoid shell quote issues
- For `fields` updates, send full field set (not partial merge)

### 2.3 Records

```bash
python scripts/pb_records.py list posts --filter 'status="published"' --sort "-created" --expand "author"
python scripts/pb_records.py get posts <recordId>
python scripts/pb_records.py create posts --file record.json
python scripts/pb_records.py update posts <recordId> '{"status":"published"}'
python scripts/pb_records.py delete posts <recordId>
```

### 2.4 Backups

```bash
python scripts/pb_backups.py list
python scripts/pb_backups.py create
python scripts/pb_backups.py restore <backupKey>
python scripts/pb_backups.py delete <backupKey>
```

Restore replaces all data; always create a backup before restore.

### 2.5 Migrations

Primary workflow (both modes):

1. Start PocketBase
2. Change collections via API/Admin UI
3. Commit auto-generated migration in `pb_migrations/`

Manual migration template generation (only for seed/data transform/raw SQL):

```bash
python scripts/pb_create_migration.py "backfill_user_slugs"
python scripts/pb_create_migration.py "seed_categories" --dir ./pb_migrations
```

## 3. Verification

After schema or rule changes, run:

1. `python scripts/pb_collections.py get <collection>`
2. CRUD smoke test (create/list/get/update/delete)
3. Rule test with non-superuser token

Rule-denied behavior reference:

- `listRule` denied -> `200` with empty `items`
- `viewRule` denied -> `404`
- `createRule` denied -> `400`
- `updateRule` denied -> `404`
- `deleteRule` denied -> `404`
- `null` rule for regular users -> `403`

E2E tests are required when non-`null` API rules or custom hooks/routes exist. Use `references/e2e-testing.md` and `scripts/pb_e2e_helpers.py`.

Minimum E2E coverage:

- Unauthenticated access denied
- Authenticated allowed path succeeds
- Cross-user access blocked
- Owner spoofing in create payload blocked
- `cascadeDelete` behavior verified
- Custom route auth enforcement verified
- Hook side effects verified

## 4. Reference Map

Read only what is needed:

- Common pitfalls: `references/gotchas.md`
- API rules: `references/api-rules-guide.md`
- Collections API: `references/collections-api.md`
- Records API: `references/records-api.md`
- Auth API: `references/auth-api.md`
- Migrations (JS): `references/migrations.md`
- Field types: `references/field-types.md`
- Relation patterns: `references/relation-patterns.md`
- File handling: `references/file-handling.md`
- JSVM hooks: `references/jsvm-hooks.md`
- Go setup: `references/go-framework.md`
- Go migrations: `references/go-migrations.md`
- Go hooks/routes: `references/go-hooks-routes.md`
- E2E testing: `references/e2e-testing.md`

Frontend integration work belongs to `pb-react-spa` skill.
