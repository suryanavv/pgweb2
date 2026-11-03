import type {
  AuditEntry,
  BrowseSearch,
  ColumnInfo,
  ConnectionInput,
  ConnectionRead,
  ConnectionUpdate,
  QueryResult,
  Role,
  TableColumns,
  TableChanges,
  TableChangesResult,
  TableInfo,
  TableRowsResult,
  TableStructure,
  TxInfo,
  TxQueryResult,
  User,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Signed out (cookie expired or revoked) while the app was open.
let unauthListener: (() => void) | null = null
export function onUnauthenticated(fn: () => void) {
  unauthListener = fn
  return () => {
    if (unauthListener === fn) unauthListener = null
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  // The API rejects state-changing requests without this; a cross-site form can't set it.
  headers.set('X-Requested-With', 'pgweb2')
  const res = await fetch(path, { ...init, headers, credentials: 'include', cache: 'no-store' })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
      // FastAPI validation errors: [{loc, msg}]
      if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg?: string }) => d.msg).join('; ')
    } catch {
      /* non-JSON error body */
    }
    // Auth endpoints answer 401 as a normal result (the 'am I signed in?' probe, a wrong password); only a
    // 401 from a data endpoint means a session that was valid has ended.
    if (res.status === 401 && !path.startsWith('/api/auth/')) unauthListener?.()
    throw new ApiError(res.status, detail)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) })
const enc = encodeURIComponent
const conn = (id: string) => `/api/connections/${id}`

export const api = {
  // --- auth ---
  login: (email: string, password: string) => request<User>('/api/auth/login', json('POST', { email, password })),
  logout: () => request<void>('/api/auth/logout', json('POST')),
  me: () => request<User>('/api/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('/api/auth/password', json('POST', { current_password, new_password })),

  // --- admin ---
  listUsers: () => request<User[]>('/api/users'),
  createUser: (u: { email: string; name: string; password: string; role: Role }) => request<User>('/api/users', json('POST', u)),
  updateUser: (id: string, patch: Partial<{ name: string; role: Role; is_active: boolean; password: string }>) =>
    request<User>(`/api/users/${id}`, json('PATCH', patch)),
  listAudit: (p: { before_id?: number; user_email?: string; action?: string } = {}) => {
    const q = new URLSearchParams({ limit: '100' })
    for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== '') q.set(k, String(v))
    return request<AuditEntry[]>(`/api/audit?${q}`)
  },

  // --- connections ---
  listConnections: () => request<ConnectionRead[]>('/api/connections'),
  createConnection: (input: ConnectionInput) => request<ConnectionRead>('/api/connections', json('POST', input)),
  updateConnection: (id: string, patch: ConnectionUpdate) => request<ConnectionRead>(conn(id), json('PATCH', patch)),
  deleteConnection: (id: string) => request<void>(conn(id), json('DELETE')),
  switchDatabase: (id: string, database: string) => request<ConnectionRead>(`${conn(id)}/database`, json('POST', { database })),

  // --- schema & data ---
  listDatabases: (id: string) => request<string[]>(`${conn(id)}/databases`),
  listTables: (id: string) => request<TableInfo[]>(`${conn(id)}/tables`),
  listColumns: (id: string, schema: string, table: string) => request<ColumnInfo[]>(`${conn(id)}/tables/${enc(schema)}/${enc(table)}/columns`),
  allColumns: (id: string) => request<TableColumns[]>(`${conn(id)}/columns`),
  tableStructure: (id: string, schema: string, table: string) =>
    request<TableStructure>(`${conn(id)}/tables/${enc(schema)}/${enc(table)}/structure`),
  runQuery: (id: string, sql: string, limit = 1000) => request<QueryResult>(`${conn(id)}/query`, json('POST', { sql, limit })),
  browseTable: (
    id: string,
    schema: string,
    table: string,
    opts: { limit: number; offset: number; orderBy?: string; desc?: boolean; filter?: string },
  ) => {
    const q = new URLSearchParams({ limit: String(opts.limit), offset: String(opts.offset) })
    if (opts.orderBy) q.set('order_by', opts.orderBy)
    if (opts.desc) q.set('desc', 'true')
    if (opts.filter) q.set('filter', opts.filter)
    return request<TableRowsResult>(`${conn(id)}/tables/${enc(schema)}/${enc(table)}/rows?${q}`)
  },
  searchTable: (id: string, schema: string, table: string, search: BrowseSearch) =>
    request<TableRowsResult>(`${conn(id)}/tables/${enc(schema)}/${enc(table)}/rows/search`, json('POST', search)),
  applyTableChanges: (id: string, schema: string, table: string, changes: TableChanges) =>
    request<TableChangesResult>(`${conn(id)}/tables/${enc(schema)}/${enc(table)}/changes`, json('POST', changes)),

  // --- manual transactions ---
  listTransactions: () => request<TxInfo[]>('/api/transactions'),
  beginTransaction: (id: string) => request<TxInfo>(`${conn(id)}/transactions`, json('POST')),
  txQuery: (txId: string, sql: string, limit = 1000) =>
    request<TxQueryResult>(`/api/transactions/${txId}/query`, json('POST', { sql, limit })),
  commit: (txId: string) => request<void>(`/api/transactions/${txId}/commit`, json('POST')),
  rollback: (txId: string) => request<void>(`/api/transactions/${txId}/rollback`, json('POST')),
}
