import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { LoginScreen } from '@/components/auth/login-screen'
import { ConnectionsScreen } from '@/components/connections/connections-screen'
import { Workspace } from '@/components/workspace/workspace'
import { useConnectionList } from '@/hooks/use-connections'
import { api, ApiError, onUnauthenticated } from '@/lib/api'
import { useActive } from '@/stores/connections'
import { useWorkspace } from '@/stores/workspace'
import { subscribeAuthChanges } from '@/lib/auth-events'

const Splash = () => (
  <div className="grid h-dvh place-items-center text-muted-foreground">
    <Loader2 className="size-6 animate-spin" />
  </div>
)

export default function App() {
  const qc = useQueryClient()
  const { activeId, setActive } = useActive()
  const [verifiedUserId, setVerifiedUserId] = useState<string | null | undefined>(undefined)
  const [authRefreshPending, setAuthRefreshPending] = useState(false)
  const authRefreshSequence = useRef(0)
  const clearAccountState = useCallback(() => {
    qc.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' })
    useWorkspace.setState({ byConn: {}, tableDrafts: {}, ownerUserId: null })
    useActive.getState().setActive(null)
  }, [qc])

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.me().catch((e) => (e instanceof ApiError && e.status === 401 ? null : Promise.reject(e))),
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
    retry: false,
  })

  const refreshAfterAuthChange = useCallback(() => {
    const sequence = ++authRefreshSequence.current
    setAuthRefreshPending(true)
    clearAccountState()
    qc.setQueryData(['me'], null)
    void qc.invalidateQueries({ queryKey: ['me'], exact: true }).finally(() => {
      if (authRefreshSequence.current === sequence) setAuthRefreshPending(false)
    })
  }, [clearAccountState, qc])

  // The session can end while the app is open (idle timeout, deactivated, signed out elsewhere).
  useEffect(
    () =>
      onUnauthenticated(() => {
        clearAccountState()
        qc.setQueryData(['me'], null)
      }),
    [clearAccountState, qc],
  )

  useEffect(() => subscribeAuthChanges(refreshAfterAuthChange), [refreshAfterAuthChange])

  // Gate the app on the server-verified identity before restoring a persisted workspace.
  useEffect(() => {
    if (me.isLoading || me.isFetching || me.error) return
    const userId = me.data?.id ?? null
    if (useWorkspace.getState().ownerUserId !== userId) {
      clearAccountState()
      useWorkspace.getState().setOwnerUserId(userId)
    }
    if (verifiedUserId !== userId) {
      // The app stays behind the splash until this server-verified identity has been reconciled with local state.
      // oxlint-disable-next-line react/set-state-in-effect
      setVerifiedUserId(userId)
    }
  }, [me.isLoading, me.isFetching, me.error, me.data?.id, clearAccountState, verifiedUserId])

  const signedIn = !!me.data
  const connections = useConnectionList(signedIn)
  const active = connections.data?.find((c) => c.id === activeId)

  // A remembered connection that's gone (deleted, unshared) shouldn't strand the user on a splash screen.
  useEffect(() => {
    if (signedIn && activeId && connections.data && !connections.isFetching && !active) setActive(null)
  }, [signedIn, activeId, connections.data, connections.isFetching, active, setActive])

  const currentUserId = me.data?.id ?? null
  if (me.isLoading) return <Splash />
  if (me.error) return <div className="grid h-dvh place-items-center p-6 text-center text-sm text-destructive">Can't reach the server: {me.error.message}</div>
  if (authRefreshPending) return <Splash />
  if (verifiedUserId !== currentUserId) return <Splash />
  if (!me.data) return <LoginScreen />
  if (activeId && connections.isLoading) return <Splash />
  if (!active) return <ConnectionsScreen me={me.data} />
  return <Workspace key={active.id} conn={active} me={me.data} />
}
