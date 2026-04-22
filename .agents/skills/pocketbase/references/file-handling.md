# File Handling Reference

Uploading, accessing, and managing files in PocketBase v0.23+.

> **File field configuration:** Read `references/field-types.md` — `file` field properties.

---

## File Field Configuration

```json
{
  "name": "documents",
  "type": "file",
  "maxSelect": 5,
  "maxSize": 10485760,
  "mimeTypes": ["application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"],
  "thumbs": ["100x100", "300x200", "0x400"],
  "protected": false
}
```

| Property | Description |
|----------|-------------|
| `maxSelect` | `1` = single file, `>1` = multiple files |
| `maxSize` | Max file size in bytes (e.g., `10485760` = 10 MB) |
| `mimeTypes` | Allowed MIME types; empty array = allow all |
| `thumbs` | Pre-generated thumbnail sizes (see [Thumbnails](#thumbnails)) |
| `protected` | `true` = require auth token to access file URL |

---

## Upload Files

### JS SDK

Pass a `File` or `Blob` object. The SDK automatically sends `multipart/form-data`.

```ts
// Single file upload
const fileInput = document.querySelector('input[type="file"]')
const file = fileInput.files[0]

const record = await pb.collection('documents').create({
  title: 'My Document',
  file: file,
})

// Multiple files
const files = fileInput.files  // FileList
const record = await pb.collection('documents').create({
  title: 'My Documents',
  attachments: [...files],
})
```

### Append Files to Existing Record (v0.23+)

```ts
// Append a new file without removing existing ones
await pb.collection('documents').update('RECORD_ID', {
  'attachments+': [newFile],
})

// Remove a specific file by filename
await pb.collection('documents').update('RECORD_ID', {
  'attachments-': ['existing-file.pdf'],
})

// WARNING: plain assignment REPLACES all existing files
await pb.collection('documents').update('RECORD_ID', {
  attachments: [newFile],  // removes all existing files!
})
```

### curl Example

```bash
curl -X POST "http://127.0.0.1:8090/api/collections/documents/records" \
  -H "Authorization: Bearer TOKEN" \
  -F "title=My Document" \
  -F "file=@/path/to/file.pdf"
```

---

## File URLs

### URL Format

```
{PB_URL}/api/files/{collectionIdOrName}/{recordId}/{filename}
```

Example:
```
http://127.0.0.1:8090/api/files/documents/abc123/report.pdf
```

### JS SDK Helper

```ts
// Basic URL
const url = pb.files.getURL(record, record.file)

// With thumbnail
const thumbUrl = pb.files.getURL(record, record.avatar, { thumb: '100x100' })
```

### Manual Construction

```ts
const baseUrl = pb.baseURL
const url = `${baseUrl}/api/files/${record.collectionId}/${record.id}/${record.file}`
```

---

## Thumbnails

Thumbnails are generated lazily on first request and cached. Define available sizes in the `thumbs` field property.

### Thumb Parameter Formats

| Format | Description |
|--------|-------------|
| `WxH` | Crop to exact size (center crop) |
| `Wx0` | Resize to width W, maintain aspect ratio |
| `0xH` | Resize to height H, maintain aspect ratio |
| `WxHt` | Crop from **top** |
| `WxHb` | Crop from **bottom** |
| `WxHf` | **Force** exact dimensions (may stretch/distort) |

```ts
pb.files.getURL(record, record.image, { thumb: '200x200' })   // center crop
pb.files.getURL(record, record.image, { thumb: '400x0' })     // width-only resize
pb.files.getURL(record, record.image, { thumb: '200x200t' })  // crop from top
```

**Note:** Only sizes declared in the `thumbs` field configuration are pre-generated. Requesting an undeclared size will generate it on-the-fly (slower first request).

---

## Protected Files

When `protected: true` is set on a file field, the file URL requires a short-lived token.

### Get a File Token (JS SDK)

```ts
// Generate a temporary token (~2 minutes validity)
const token = await pb.files.getToken()

// Append to file URL
const url = pb.files.getURL(record, record.document, { token })
// Results in: .../document.pdf?token=XXXXX
```

### Token Behavior

- Token is valid for approximately **2 minutes**
- Token is **single-use** (invalidated after first successful use)
- The requesting user must be authenticated to obtain a token
- Token grants access to all protected files for that user's session

### Protected File Access Pattern

```ts
async function getProtectedFileUrl(record, fieldValue) {
  const token = await pb.files.getToken()
  return pb.files.getURL(record, fieldValue, { token })
}

// Usage
const url = await getProtectedFileUrl(record, record.privateDocument)
window.open(url)  // opens within ~2 minute window
```

---

## Modify Existing Files

```ts
// APPEND one or more new files (keeps existing)
await pb.collection('posts').update('RECORD_ID', {
  'images+': [file1, file2],
})

// REMOVE specific files by filename
await pb.collection('posts').update('RECORD_ID', {
  'images-': ['old-image.jpg'],
})

// REMOVE ALL files (set to empty)
await pb.collection('posts').update('RECORD_ID', {
  images: [],
})

// REPLACE ALL files with new ones
await pb.collection('posts').update('RECORD_ID', {
  images: [newFile1, newFile2],  // replaces everything
})
```

> **v0.23+ behavior change:** Plain assignment replaces all existing files. Always use `field+` to append.

---

## Storage Backends

### Local Storage (Default)

Files are stored at `pb_data/storage/{collectionId}/{recordId}/{filename}`.

No configuration needed. Suitable for single-server deployments.

### S3-Compatible Storage

Configure via the PocketBase Admin UI (Settings → File storage) or environment:

- Amazon S3
- Cloudflare R2
- MinIO
- Any S3-compatible provider

Once configured, all new uploads go to S3. Existing local files are not migrated automatically.

**Backup consideration:** When using S3, backups via `pb_backups.py` include only the database, not S3 files. Back up S3 separately.
