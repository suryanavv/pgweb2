import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ExistingRowChange, TableChanges } from '@/lib/types'

export type Tab =
  | { id: string; kind: 'table'; schema: string; table: string }
  // txId: a manual transaction this tab holds open on the server (re-attached after a reload)
  | { id: string; kind: 'query'; title: string; sql: string; txId?: string }

interface ConnWorkspace {
  tabs: Tab[]
  activeId: string | null
}

export interface StagedRowChange extends ExistingRowChange {
  original: Record<string, unknown>
}

export interface TableDraft {
  updates: Record<string, StagedRowChange>
  deletes: Record<string, StagedRowChange>
  inserts: { id: string; values: Record<string, unknown> }[]
}

export const emptyTableDraft = (): TableDraft => ({ updates: {}, deletes: {}, inserts: [] })
export const tableDraftKey = (connId: string, schema: string, table: string) => JSON.stringify([connId, schema, table])
export const draftCount = (draft?: TableDraft) =>
  draft ? Object.keys(draft.updates).length + Object.keys(draft.deletes).length + draft.inserts.length : 0
export const apiTableChanges = (draft: TableDraft): TableChanges => ({
  updates: Object.values(draft.updates).map(({ primary_key, version, values }) => ({ primary_key, version, values })),
  inserts: draft.inserts.map(({ values }) => ({ values })),
  deletes: Object.values(draft.deletes).map(({ primary_key, version }) => ({ primary_key, version, values: {} })),
})

interface WorkspaceState {
  byConn: Record<string, ConnWorkspace>
  tableDrafts: Record<string, TableDraft>
  ownerUserId: string | null
  setOwnerUserId: (userId: string | null) => void
  setTableDraft: (tabId: string, draft: TableDraft) => void
  clearTableDraft: (tabId: string) => void
  openTable: (connId: string, schema: string, table: string) => void
  openQuery: (connId: string, sql?: string) => void
  closeTab: (connId: string, tabId: string) => void
  closeOthers: (connId: string, tabId: string) => void
  setActive: (connId: string, tabId: string) => void
  setSql: (connId: string, tabId: string, sql: string) => void
  setTx: (connId: string, tabId: string, txId: string | null) => void
  dropConnection: (connId: string) => void
}

const EMPTY: ConnWorkspace = { tabs: [], activeId: null }
let seq = 0
const newId = () => `${Date.now().toString(36)}${(seq++).toString(36)}`

export const useWorkspace = create<WorkspaceState>()(
  persist(
    (set) => {
      const patch = (connId: string, fn: (w: ConnWorkspace) => ConnWorkspace) =>
        set((s) => ({ byConn: { ...s.byConn, [connId]: fn(s.byConn[connId] ?? EMPTY) } }))

      return {
        byConn: {},
        tableDrafts: {},
        ownerUserId: null,
        setOwnerUserId: (ownerUserId) => set({ ownerUserId }),
        setTableDraft: (tabId, draft) => set((s) => ({ tableDrafts: { ...s.tableDrafts, [tabId]: draft } })),
        clearTableDraft: (tabId) => set((s) => {
          const { [tabId]: _, ...rest } = s.tableDrafts
          return { tableDrafts: rest }
        }),
        openTable: (connId, schema, table) =>
          patch(connId, (w) => {
            const id = `t:${schema}.${table}`
            return w.tabs.some((t) => t.id === id)
              ? { ...w, activeId: id }
              : { tabs: [...w.tabs, { id, kind: 'table', schema, table }], activeId: id }
          }),
        openQuery: (connId, sql = '') =>
          patch(connId, (w) => {
            const n = w.tabs.filter((t) => t.kind === 'query').length + 1
            const id = `q:${newId()}`
            return { tabs: [...w.tabs, { id, kind: 'query', title: `Query ${n}`, sql }], activeId: id }
          }),
        closeTab: (connId, tabId) =>
          patch(connId, (w) => {
            const i = w.tabs.findIndex((t) => t.id === tabId)
            const tabs = w.tabs.filter((t) => t.id !== tabId)
            const activeId = w.activeId === tabId ? (tabs[Math.min(i, tabs.length - 1)]?.id ?? null) : w.activeId
            return { tabs, activeId }
          }),
        closeOthers: (connId, tabId) =>
          patch(connId, (w) => ({ tabs: w.tabs.filter((t) => t.id === tabId), activeId: tabId })),
        setActive: (connId, tabId) => patch(connId, (w) => ({ ...w, activeId: tabId })),
        setSql: (connId, tabId, sql) =>
          patch(connId, (w) => ({
            ...w,
            tabs: w.tabs.map((t) => (t.id === tabId && t.kind === 'query' ? { ...t, sql } : t)),
          })),
        setTx: (connId, tabId, txId) =>
          patch(connId, (w) => ({
            ...w,
            tabs: w.tabs.map((t) => (t.id === tabId && t.kind === 'query' ? { ...t, txId: txId ?? undefined } : t)),
          })),
        dropConnection: (connId) =>
          set((s) => {
            const { [connId]: _, ...rest } = s.byConn
            return { byConn: rest }
          }),
      }
    },
    { name: 'pgweb2.workspace', partialize: (s) => ({ byConn: s.byConn, ownerUserId: s.ownerUserId }) },
  ),
)

export const useConnWorkspace = (connId: string) => useWorkspace((s) => s.byConn[connId] ?? EMPTY)
