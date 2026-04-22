# Field Types Reference

PocketBase v0.23+ field types for collection schemas.

> **v0.23+ API Note:** Field properties are **flat** — there is no `options` wrapper.
> WRONG: `{"type": "select", "options": {"values": ["a", "b"], "maxSelect": 1}}`
> CORRECT: `{"type": "select", "values": ["a", "b"], "maxSelect": 1}`

## Table of Contents

- [text](#text)
- [number](#number)
- [bool](#bool)
- [email](#email)
- [url](#url)
- [date](#date)
- [select](#select)
- [json](#json)
- [file](#file)
- [relation](#relation)
- [editor](#editor)
- [autodate](#autodate)
- [password](#password)
- [Field Common Properties](#field-common-properties)

---

## text

Plain text field.

```json
{
  "name": "title",
  "type": "text",
  "required": true,
  "min": 1,
  "max": 500,
  "pattern": "^[a-zA-Z]+$",
  "autogeneratePattern": "",
  "primaryKey": false,
  "hidden": false
}
```

| Property | Type | Description |
|----------|------|-------------|
| min | Number | Minimum characters |
| max | Number | Maximum characters (0 = no limit) |
| pattern | String | Regex validation pattern |
| autogeneratePattern | String | Auto-generate pattern (e.g., `[a-z0-9]{15}` for IDs) |
| primaryKey | Boolean | Mark as primary key |

## number

Numeric field (integer or float).

```json
{
  "name": "price",
  "type": "number",
  "required": true,
  "min": 0,
  "max": 999999,
  "onlyInt": false
}
```

| Property | Type | Description |
|----------|------|-------------|
| min | Number | Minimum value |
| max | Number | Maximum value (0 = no limit) |
| onlyInt | Boolean | Accept only integers |

## bool

Boolean (true/false) field.

```json
{
  "name": "published",
  "type": "bool"
}
```

No additional properties. Default value is `false`.

## email

Email address field with built-in format validation.

```json
{
  "name": "contactEmail",
  "type": "email",
  "required": true,
  "exceptDomains": [],
  "onlyDomains": []
}
```

| Property | Type | Description |
|----------|------|-------------|
| exceptDomains | Array | Block these domains |
| onlyDomains | Array | Allow only these domains |

## url

URL field with built-in format validation.

```json
{
  "name": "website",
  "type": "url",
  "exceptDomains": [],
  "onlyDomains": []
}
```

Same domain filtering as `email` type.

## date

Date/datetime field (stored as UTC string).

```json
{
  "name": "publishDate",
  "type": "date",
  "min": "2024-01-01 00:00:00.000Z",
  "max": "2030-12-31 23:59:59.999Z"
}
```

| Property | Type | Description |
|----------|------|-------------|
| min | String | Minimum date (ISO format) |
| max | String | Maximum date (ISO format) |

Format: `YYYY-MM-DD HH:MM:SS.sssZ`

## select

Single or multi-value selection from predefined values.

```json
{
  "name": "status",
  "type": "select",
  "required": true,
  "values": ["draft", "published", "archived"],
  "maxSelect": 1
}
```

| Property | Type | Description |
|----------|------|-------------|
| values | Array | List of allowed values |
| maxSelect | Number | Max selections (1 = single select, >1 = multi select) |

Multi-select stores as JSON array: `["value1", "value2"]`.

## json

Arbitrary JSON data.

```json
{
  "name": "metadata",
  "type": "json",
  "maxSize": 65536
}
```

| Property | Type | Description |
|----------|------|-------------|
| maxSize | Number | Max JSON size in bytes (0 = default ~2MB) |

Stored as raw JSON. Can hold objects, arrays, or primitives.

## file

File upload field.

```json
{
  "name": "documents",
  "type": "file",
  "required": false,
  "maxSelect": 5,
  "maxSize": 10485760,
  "mimeTypes": ["application/pdf", "image/png", "image/jpeg"],
  "thumbs": ["100x100", "200x200"]
}
```

| Property | Type | Description |
|----------|------|-------------|
| maxSelect | Number | Max files (1 = single, >1 = multi) |
| maxSize | Number | Max file size in bytes |
| mimeTypes | Array | Allowed MIME types (empty = all) |
| thumbs | Array | Auto-generate thumbnail sizes (e.g., `"100x100"`, `"0x300"`) |
| protected | Boolean | Require auth token to access |

File URLs: `{PB_URL}/api/files/{collectionId}/{recordId}/{filename}`

> **File operations guide:** Read `references/file-handling.md`

## relation

Reference to records in another collection.

```json
{
  "name": "author",
  "type": "relation",
  "required": true,
  "collectionId": "TARGET_COLLECTION_ID",
  "cascadeDelete": false,
  "maxSelect": 1,
  "minSelect": 0
}
```

| Property | Type | Description |
|----------|------|-------------|
| collectionId | String | Target collection ID |
| cascadeDelete | Boolean | Delete related records on delete |
| maxSelect | Number | Max relations (1 = single, >1 = multi) |
| minSelect | Number | Min required relations |

Single relation stores a record ID string. Multi stores an array of IDs.

Use `expand` query param to include full related records.

## editor

Rich text (HTML) editor field.

```json
{
  "name": "content",
  "type": "editor",
  "maxSize": 0,
  "convertURLs": false
}
```

| Property | Type | Description |
|----------|------|-------------|
| maxSize | Number | Max content size in bytes (0 = default ~2MB) |
| convertURLs | Boolean | Convert relative URLs to absolute |

Stored as sanitized HTML.

## autodate

Auto-managed date field. Typically used for `created` and `updated` timestamps.

```json
{
  "name": "created",
  "type": "autodate",
  "onCreate": true,
  "onUpdate": false
}
```

| Property | Type | Description |
|----------|------|-------------|
| onCreate | Boolean | Set date on record creation |
| onUpdate | Boolean | Update date on record update |

System collections include `created` (onCreate only) and `updated` (both) by default.

## password

Hashed password field. Only available in auth-type collections. Cannot be read via API.

```json
{
  "name": "password",
  "type": "password",
  "min": 8,
  "max": 72,
  "required": true
}
```

## Field Common Properties

All field types share these properties:

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| name | String | — | Field name (required) |
| type | String | — | Field type (required) |
| required | Boolean | false | Value required |
| hidden | Boolean | false | Hidden from API responses |
| presentable | Boolean | false | Used as display value in the admin UI |
| system | Boolean | false | System-managed (cannot be modified) |
| id | String | auto | Field ID (auto-generated) |
