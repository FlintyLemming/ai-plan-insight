# JSVM Hooks Reference

Server-side JavaScript hooks for PocketBase v0.23+ using the built-in `goja` JS runtime.

> **Critical:** goja is **ES5** — not Node.js. No npm, no async/await, no setTimeout. See [Critical Limitations](#critical-limitations).

---

## Setup

1. Create `pb_hooks/` directory in your PocketBase project root
2. Place `*.pb.js` files in that directory (must use `.pb.js` extension)
3. PocketBase watches for changes — **edits** to existing files are hot-reloaded, but **new files** may not be picked up without a restart
4. If a hook does not fire after creating a new file:
   - Check `pb.log` for syntax errors (JSVM parse failures are logged there)
   - Verify the file uses `*.pb.js` extension (not `.js` alone)
   - Restart PocketBase: `kill $(pgrep -f 'pocketbase serve') && nohup ./pocketbase serve --http=127.0.0.1:8090 > pb.log 2>&1 &`

### TypeScript Types

For IDE autocompletion, download the types file:

```bash
# Place alongside your hooks files for editor support
curl -o pb_hooks/types.d.ts https://pocketbase.io/types.d.ts
```

```js
/// <reference path="./types.d.ts" />
// Your hook code here
```

---

## Event Hooks

Hooks intercept PocketBase lifecycle events. Always call `e.next()` to continue the chain.

### Record Lifecycle

Each lifecycle has three phases:

| Phase | When | Use For |
|-------|------|---------|
| `onRecordCreate(fn)` | Before DB write | Validate, transform, reject |
| `onRecordCreateExecute(fn)` | After DB write (in transaction) | Send notifications, update related records |
| `onRecordAfterCreateSuccess(fn)` | After transaction commits | Trigger external integrations |

Same pattern for `Update` and `Delete`.

### DB Hooks vs Request Hooks

PocketBase has **two** hook categories. Using the wrong one is a common mistake:

| | Request Hooks | DB Hooks |
|-|---------------|----------|
| **Names** | `onRecordCreateRequest`, `onRecordUpdateRequest`, `onRecordDeleteRequest` | `onRecordCreate`, `onRecordUpdate`, `onRecordDelete` |
| **Fires on** | REST API requests only | ALL saves (API, hooks, migrations, `$app.save()`) |
| **Has `e.auth`?** | Yes — `$app.requestInfo(e.request).auth` | No — no request context |
| **Has `e.request`?** | Yes | No |
| **Use for** | Access control, audit logging with user info | Data transforms, computed fields, cascades |

**WRONG — accessing auth in a DB hook (will be `undefined`):**
```js
onRecordCreate((e) => {
    const info = $app.requestInfo(e.request)  // ERROR: e.request is undefined
    const user = info.auth
    e.next()
}, "posts")
```

**CORRECT — use a Request hook when you need auth context:**
```js
onRecordCreateRequest((e) => {
    const info = $app.requestInfo(e.request)
    const user = info.auth
    if (!user || user.getString("role") !== "admin") {
        throw new ForbiddenError("Admin only")
    }
    e.next()
}, "posts")
```

**For custom endpoints**, access auth via `$app.requestInfo(e.request).auth` (see [Request & Response Handling](#reading-request-data)).

Request hook names: `onRecordCreateRequest`, `onRecordUpdateRequest`, `onRecordDeleteRequest`, `onRecordAuthWithPasswordRequest`, `onRecordAuthWithOAuth2Request`, `onRecordAuthRefreshRequest`

DB hook names: `onRecordCreate`, `onRecordCreateExecute`, `onRecordAfterCreateSuccess`, `onRecordUpdate`, `onRecordUpdateExecute`, `onRecordAfterUpdateSuccess`, `onRecordDelete`, `onRecordDeleteExecute`, `onRecordAfterDeleteSuccess`

### The `e.next()` Pattern

```js
/// <reference path="./types.d.ts" />

// Add a computed field before saving
onRecordCreate((e) => {
    // Access the record being created
    const record = e.record

    // Modify fields before DB write
    record.set("slug", record.getString("title").toLowerCase().replace(/\s+/g, "-"))

    // REQUIRED: continue to database write
    e.next()

    // Code after e.next() runs after the DB operation completes
    // but before the response is sent to the client
}, "posts")  // Optional: filter to specific collection
```

### Collection Filter

Pass a collection name as second argument to scope the hook:

```js
onRecordCreate((e) => {
    e.next()
}, "posts")  // Only fires for posts collection

onRecordDelete((e) => {
    e.next()
})  // Fires for ALL collections if no filter specified
```

### Reject a Request

Throw an error to abort the operation:

```js
onRecordCreate((e) => {
    if (e.record.getString("title").length < 3) {
        throw new BadRequestError("Title must be at least 3 characters")
    }
    e.next()
}, "posts")
```

### Auth Hooks

```js
// Fires after successful authentication
onRecordAuthWithPasswordRequest((e) => {
    const record = e.record
    // Log login, check account status, etc.
    e.next()
}, "users")

// Fires before OAuth2 auth
onRecordAuthWithOAuth2Request((e) => {
    // e.oAuth2User contains provider data
    // e.isNewRecord is true if creating a new user
    e.next()
}, "users")
```

---

## Custom Endpoints

Register custom HTTP endpoints using `routerAdd`.

### Basic Endpoint

```js
routerAdd("GET", "/api/hello", (e) => {
    return e.json(200, {"message": "Hello, World!"})
})
```

### Path Parameters

Use `{paramName}` syntax (v0.23+):

```js
routerAdd("GET", "/api/greet/{name}", (e) => {
    const name = e.request.pathValue("name")
    return e.string(200, "Hello, " + name + "!")
})
```

### Wildcard Path

```js
routerAdd("GET", "/api/proxy/{path...}", (e) => {
    const path = e.request.pathValue("path")
    // handle wildcard path
    return e.json(200, {path: path})
})
```

### POST with Body

```js
routerAdd("POST", "/api/custom", (e) => {
    const info = $app.requestInfo(e.request)
    const body = info.body  // parsed JSON body

    const name = body["name"] || "Anonymous"
    return e.json(200, {greeting: "Hello, " + name})
})
```

---

## Request & Response Handling

### Reading Request Data

```js
routerAdd("POST", "/api/example", (e) => {
    // Request object
    const req = e.request

    // Query parameters
    const page = req.url.query().get("page") || "1"

    // Headers
    const auth = req.header.get("Authorization")

    // Parsed body (JSON or form data)
    const info = $app.requestInfo(e.request)
    const body = info.body
    const title = body["title"]

    // Auth record (if authenticated)
    const authRecord = info.auth  // null if not authenticated

    return e.json(200, {page: page, title: title})
})
```

### Response Methods

```js
e.json(200, {key: "value"})           // JSON response
e.string(200, "plain text")           // Plain text response
e.html(200, "<h1>Hello</h1>")         // HTML response
e.redirect(302, "https://example.com") // Redirect
e.noContent(204)                       // No content
```

---

## Built-in Middleware

Apply middleware to protect or enhance routes.

```js
// Require any authenticated user
routerAdd("GET", "/api/protected", (e) => {
    const info = $app.requestInfo(e.request)
    const user = info.auth  // guaranteed to be non-null
    return e.json(200, {userId: user.id})
}, $apis.requireAuth())

// Require superuser authentication
routerAdd("GET", "/api/admin-only", (e) => {
    return e.json(200, {message: "Admin area"})
}, $apis.requireSuperuserAuth())

// Enable gzip compression
routerAdd("GET", "/api/large-response", (e) => {
    return e.json(200, {data: "lots of data"})
}, $apis.gzip())

// Require auth from specific collection
routerAdd("GET", "/api/managers-only", (e) => {
    return e.json(200, {ok: true})
}, $apis.requireAuth("managers"))
```

---

## Available Globals

| Global | Description |
|--------|-------------|
| `$app` | Main app instance — find/save/delete records, query DB |
| `$http` | HTTP client for external requests |
| `$security` | Crypto utilities — hash, encrypt, JWT, random token |
| `$mails` | Email sending |
| `$os` | OS operations — file system, env vars, exec |
| `$filesystem` | File storage operations |
| `$tokens` | JWT token generation for records |
| `$template` | HTML template rendering |
| `$dbx` | Query builder expressions |
| `__hooks` | Absolute path to the `pb_hooks/` directory |

### Common `$app` Methods

```js
// Record operations
$app.findRecordById("posts", "RECORD_ID")
$app.findFirstRecordByFilter("posts", "status = {:s}", {s: "published"})
$app.findRecordsByFilter("posts", "author = {:id}", {id: userId}, "-created", 10, 0)
$app.save(record)
$app.delete(record)

// Collection operations
$app.findCollectionByNameOrId("posts")

// Raw DB query
$app.db().newQuery("SELECT id FROM posts WHERE status = 'published'").column()
```

---

## JSVM Record API

The JSVM provides both a generic getter and typed getters for record fields:

```js
// Generic getter — returns untyped value
record.get("fieldName")

// Typed getters — return correctly typed values
record.getString("name")        // string
record.getInt("count")          // integer
record.getFloat("price")        // float
record.getBool("active")        // boolean
record.getStringSlice("tags")   // string array
record.getDateTime("due_date")  // DateTime object

// Setter
record.set("fieldName", value)
```

> **Note:** JSVM uses **camelCase** (`getString`, `getInt`). Go uses **PascalCase** (`GetString`, `GetInt`). Do not mix them — Go getters will not work in JSVM hooks and vice versa.

Both `record.get("field")` and typed getters like `record.getString("field")` are available in JSVM. Use typed getters when you need type safety; use `record.get()` for dynamic access.

---

## External HTTP Requests

```js
const response = $http.send({
    url: "https://api.example.com/webhook",
    method: "POST",
    body: JSON.stringify({event: "post_created", postId: record.id}),
    headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + $os.getenv("API_SECRET"),
    },
    timeout: 10,  // seconds
})

// Response properties
response.statusCode  // HTTP status
response.body        // Response body string
response.headers     // Response headers object

if (response.statusCode !== 200) {
    throw new Error("Webhook failed: " + response.body)
}
```

---

## Cron Jobs

Schedule recurring tasks.

```js
// Standard cron syntax: minute hour day month weekday
cronAdd("daily-cleanup", "0 2 * * *", () => {
    // Runs at 2:00 AM every day
    const cutoff = new Date()
    cutoff.setDate(cutoff.getDate() - 30)

    const old = $app.findRecordsByFilter(
        "logs",
        "created < {:cutoff}",
        {cutoff: cutoff.toISOString()},
        "",   // no sort
        1000, // limit
        0     // offset
    )

    for (const record of old) {
        $app.delete(record)
    }
})

// Remove a cron job
cronRemove("daily-cleanup")
```

---

## Shared Code

Share utilities across multiple hook files.

**`pb_hooks/utils.js`:**
```js
module.exports = {
    slugify: function(text) {
        return text.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "")
    },
    isAdmin: function(authRecord) {
        return authRecord && authRecord.getString("role") === "admin"
    }
}
```

**`pb_hooks/posts.pb.js`:**
```js
/// <reference path="./types.d.ts" />

const utils = require(__hooks + "/utils.js")

onRecordCreate((e) => {
    const slug = utils.slugify(e.record.getString("title"))
    e.record.set("slug", slug)
    e.next()
}, "posts")
```

---

## Critical Limitations

The JSVM uses **goja** (an ES5 JavaScript engine), not Node.js or Deno.

| Limitation | Details |
|------------|---------|
| **ES5 only** | No arrow functions in some contexts, no destructuring, no template literals (limited) |
| **No npm** | Cannot import npm packages. No `node_modules`. |
| **No async/await** | All operations are synchronous. Use synchronous APIs only. |
| **No setTimeout/setInterval** | Use `cronAdd` for scheduling instead. |
| **No Promises** | All built-in APIs return values directly (not promises). |
| **CommonJS only** | Use `require()` / `module.exports`. No ES modules (`import`/`export`). |
| **Single-threaded per hook** | Each hook invocation runs synchronously. |
| **No access to Node.js globals** | No `process`, `Buffer`, `__dirname` (use `__hooks` instead). |

**Practical implications:**

```js
// WRONG — async/await not supported
onRecordCreate(async (e) => {
    const data = await fetchSomething()  // will not work
    e.next()
})

// CORRECT — use synchronous $http.send
onRecordCreate((e) => {
    const res = $http.send({url: "https://api.example.com/data", method: "GET"})
    const data = JSON.parse(res.body)
    e.record.set("extra", data.value)
    e.next()
})
```
