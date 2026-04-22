# JavaScript SDK Reference

PocketBase JS/TS SDK (pocketbase npm package) for frontend and Node.js applications.

---

## Installation & Initialization

### ESM / TypeScript

```ts
import PocketBase from 'pocketbase'
const pb = new PocketBase('http://127.0.0.1:8090')
```

### CommonJS (Node.js)

```js
const PocketBase = require('pocketbase/cjs')
const pb = new PocketBase('http://127.0.0.1:8090')
```

### Browser (UMD / CDN)

```html
<script src="https://github.com/pocketbase/js-sdk/releases/latest/download/pocketbase.umd.js"></script>
<script>
  const pb = new PocketBase('http://127.0.0.1:8090')
</script>
```

### React Native (with AsyncAuthStore)

```ts
import PocketBase, { AsyncAuthStore } from 'pocketbase'
import AsyncStorage from '@react-native-async-storage/async-storage'

const store = new AsyncAuthStore({
  save: async (serialized) => AsyncStorage.setItem('pb_auth', serialized),
  initial: () => AsyncStorage.getItem('pb_auth'),
  clear: async () => AsyncStorage.removeItem('pb_auth'),
})

const pb = new PocketBase('http://127.0.0.1:8090', store)
```

---

## CRUD Operations

### List Records (paginated)

```ts
const result = await pb.collection('posts').getList(1, 50, {
  filter: 'status = "published"',
  sort: '-created',
  expand: 'author',
  fields: 'id,title,created,expand.author.name',
})
// result.items, result.page, result.totalItems, result.totalPages
```

### List All Records (auto-paginate)

```ts
const records = await pb.collection('posts').getFullList({
  sort: '-created',
  filter: 'status = "published"',
})
// Returns array of all matching records across all pages
```

### Get First Matching Record

```ts
const record = await pb.collection('users').getFirstListItem('email = "test@example.com"', {
  expand: 'profile',
})
// Throws 404 if not found
```

### Get Single Record by ID

```ts
const record = await pb.collection('posts').getOne('RECORD_ID', {
  expand: 'author,comments_via_post',
})
```

### Create Record

```ts
const record = await pb.collection('posts').create({
  title: 'Hello World',
  content: '<p>My post</p>',
  status: 'draft',
  author: pb.authStore.record?.id,  // current user's ID
})
```

### Update Record

```ts
const record = await pb.collection('posts').update('RECORD_ID', {
  title: 'Updated Title',
  status: 'published',
})
```

### Delete Record

```ts
await pb.collection('posts').delete('RECORD_ID')
```

---

## Filter Helper

Use `pb.filter()` to safely construct filter expressions with dynamic values. This prevents filter injection attacks.

```ts
// Unsafe — do not do this
const filter = `title = "${userInput}"`  // injection risk!

// Safe — use pb.filter()
const filter = pb.filter('title = {:title} && status = {:status}', {
  title: userInput,
  status: 'published',
})
```

Named placeholders `{:name}` are automatically escaped. Supports all filter operators.

```ts
const records = await pb.collection('posts').getList(1, 30, {
  filter: pb.filter('created >= {:date} && author = {:userId}', {
    date: new Date().toISOString(),
    userId: pb.authStore.record?.id,
  }),
})
```

---

## Authentication

### Authenticate with Password

```ts
// Auth collection: users
const authData = await pb.collection('users').authWithPassword('user@example.com', 'password123')

// authData.token  — JWT token
// authData.record — user record object
// pb.authStore.isValid — true after successful auth
```

### OAuth2 Authentication

```ts
const authData = await pb.collection('users').authWithOAuth2({
  provider: 'google',
  // Opens browser popup automatically in web context
})
```

### AuthStore

The `pb.authStore` object persists authentication state.

```ts
pb.authStore.token      // current JWT token string
pb.authStore.record     // current auth record object
pb.authStore.isValid    // true if token exists and not expired

// Clear auth (logout)
pb.authStore.clear()

// Listen for auth changes
pb.authStore.onChange((token, record) => {
  console.log('Auth changed:', token ? 'logged in' : 'logged out')
})
```

By default, auth state is stored in `localStorage` (browser) or an in-memory store (Node.js).

### Refresh Auth Token

```ts
// Refresh the current token (get a new one if still valid)
try {
  const authData = await pb.collection('users').authRefresh()
} catch (e) {
  pb.authStore.clear()  // token is invalid, force logout
}
```

---

## Realtime Subscriptions

Subscribe to record changes in real time via Server-Sent Events (SSE).

### Subscribe to All Records

```ts
const unsubscribe = await pb.collection('posts').subscribe('*', (e) => {
  console.log(e.action)  // 'create' | 'update' | 'delete'
  console.log(e.record)  // the affected record
})
```

### Subscribe to a Specific Record

```ts
const unsubscribe = await pb.collection('posts').subscribe('RECORD_ID', (e) => {
  console.log(e.action, e.record)
})
```

### Unsubscribe

```ts
// Unsubscribe a specific topic
await pb.collection('posts').unsubscribe('*')
await pb.collection('posts').unsubscribe('RECORD_ID')

// Unsubscribe all subscriptions for a collection
await pb.collection('posts').unsubscribe()

// Using the returned unsubscribe function
unsubscribe()
```

### Realtime Event Format

```ts
{
  action: 'create' | 'update' | 'delete',
  record: {
    id: 'RECORD_ID',
    collectionId: '...',
    collectionName: 'posts',
    // ... record fields
    // Note: expand is NOT populated in realtime events
  }
}
```

> **Limitation:** `expand` is not supported in realtime event payloads. Fetch the full record separately if you need expanded data.

---

## File URLs

### Get File URL

```ts
// Using the SDK helper
const url = pb.files.getURL(record, record.avatar, { thumb: '100x100' })

// Manual URL construction
const url = `${pb.baseURL}/api/files/${record.collectionId}/${record.id}/${record.avatar}`
```

### Thumbnail Parameters

```ts
// Exact crop (width x height)
pb.files.getURL(record, record.image, { thumb: '200x200' })

// Fit within dimensions (no crop)
pb.files.getURL(record, record.image, { thumb: '200x0' })  // width only
pb.files.getURL(record, record.image, { thumb: '0x200' })  // height only

// Crop from top
pb.files.getURL(record, record.image, { thumb: '200x200t' })

// Crop from bottom
pb.files.getURL(record, record.image, { thumb: '200x200b' })

// Force exact size (stretches if needed)
pb.files.getURL(record, record.image, { thumb: '200x200f' })
```

### Protected Files

```ts
// Get a short-lived token (~2 min validity) for protected file access
const token = await pb.files.getToken()

// Append token to the file URL
const url = pb.files.getURL(record, record.document, { token })
```

---

## Auto-Cancellation

By default, the SDK automatically cancels the previous identical request when a new one is made (prevents race conditions on rapid UI updates).

### Disable Auto-Cancellation

```ts
// Disable globally (recommended when using TanStack Query or similar libraries)
pb.autoCancellation(false)

// Or disable per request with requestKey: null
const result = await pb.collection('posts').getList(1, 30, {
  requestKey: null,     // disable auto-cancel for this request
})

// Or provide a custom key (only cancels previous request with same key)
const result = await pb.collection('posts').getList(1, 30, {
  requestKey: 'my-posts-request',
})
```

---

## Error Handling

All SDK methods throw `ClientResponseError` on failure.

```ts
import { ClientResponseError } from 'pocketbase'

try {
  await pb.collection('posts').create({ title: '' })
} catch (e) {
  if (e instanceof ClientResponseError) {
    console.log(e.status)    // HTTP status code (e.g., 400)
    console.log(e.message)   // Error message
    console.log(e.response)  // Full response body
    console.log(e.isAbort)   // true if request was cancelled

    // Field-level validation errors
    if (e.status === 400 && e.response.data) {
      for (const [field, error] of Object.entries(e.response.data)) {
        console.log(`${field}: ${error.message}`)
      }
    }
  }
}
```

### Validation Error Structure

```ts
// e.response.data structure for validation errors
{
  "title": {
    "code": "validation_required",
    "message": "Missing required value."
  },
  "email": {
    "code": "validation_invalid_email",
    "message": "Must be a valid email address."
  }
}
```

### Handling Abort Errors

```ts
try {
  const result = await pb.collection('posts').getList(1, 30)
} catch (e) {
  if (e instanceof ClientResponseError && e.isAbort) {
    // Request was cancelled by auto-cancellation — ignore safely
    return
  }
  throw e  // re-throw real errors
}
```
