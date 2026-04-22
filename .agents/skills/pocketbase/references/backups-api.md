# Backups API Reference

PocketBase v0.23+ backup management. All endpoints require superuser authentication.

## Table of Contents

- [List Backups](#list-backups)
- [Create Backup](#create-backup)
- [Restore Backup](#restore-backup)
- [Delete Backup](#delete-backup)
- [Limitations](#limitations)

---

## List Backups

```
GET /api/backups
```

**Response (200):** Array of backup objects.
```json
[
  {
    "key": "pb_backup_20240101120000.zip",
    "size": 1234567,
    "modified": "2024-01-01 12:00:00.000Z"
  }
]
```

## Create Backup

```
POST /api/backups
```

**Body (optional):**
```json
{"name": "my_backup.zip"}
```

If `name` is omitted, PocketBase auto-generates a timestamped name.

**Response (204):** No content. Backup creation runs asynchronously.

## Restore Backup

```
POST /api/backups/{key}/restore
```

**Response (204):** No content. The server will restart after restore.

**Warning:** Restoring a backup replaces all current data. The server will be temporarily unavailable during restore.

## Delete Backup

```
DELETE /api/backups/{key}
```

**Response (204):** No content.

## Limitations

- Only one backup operation (create or restore) can run at a time.
- Backup includes the SQLite database and uploaded files.
- Restore replaces **all** data — there is no merge/partial restore.
- The server restarts after a restore; existing connections will be dropped.
- For large databases, backup creation may take significant time.
- Backup files are stored in `pb_data/backups/` on the server.
