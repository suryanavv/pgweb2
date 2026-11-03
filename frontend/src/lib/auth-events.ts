const CHANNEL_NAME = 'pgweb2-auth'
const STORAGE_KEY = 'pgweb2.auth-event'

/** Notify other tabs that the shared browser cookie may now identify a different session. */
export function publishAuthChange() {
  if (typeof BroadcastChannel !== 'undefined') {
    const channel = new BroadcastChannel(CHANNEL_NAME)
    channel.postMessage({ type: 'auth-changed' })
    channel.close()
    return
  }

  try {
    localStorage.setItem(STORAGE_KEY, `${Date.now()}-${Math.random()}`)
  } catch {
    // Private browsing can disable storage; the next focus revalidation still refreshes identity.
  }
}

export function subscribeAuthChanges(onChange: () => void) {
  if (typeof BroadcastChannel !== 'undefined') {
    const channel = new BroadcastChannel(CHANNEL_NAME)
    const listener = (event: MessageEvent<{ type?: string }>) => {
      if (event.data?.type === 'auth-changed') onChange()
    }
    channel.addEventListener('message', listener)
    return () => {
      channel.removeEventListener('message', listener)
      channel.close()
    }
  }

  const listener = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) onChange()
  }
  window.addEventListener('storage', listener)
  return () => window.removeEventListener('storage', listener)
}
