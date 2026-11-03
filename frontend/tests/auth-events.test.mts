import assert from 'node:assert/strict'
import { test } from 'node:test'
import { publishAuthChange, subscribeAuthChanges } from '../src/lib/auth-events.ts'

class FakeBroadcastChannel {
  static channels = new Set()
  listeners = new Set()

  constructor(name) {
    this.name = name
    FakeBroadcastChannel.channels.add(this)
  }

  addEventListener(_type, listener) {
    this.listeners.add(listener)
  }

  removeEventListener(_type, listener) {
    this.listeners.delete(listener)
  }

  postMessage(data) {
    for (const channel of FakeBroadcastChannel.channels) {
      if (channel !== this && channel.name === this.name) {
        queueMicrotask(() => channel.listeners.forEach((listener) => listener({ data })))
      }
    }
  }

  close() {
    FakeBroadcastChannel.channels.delete(this)
  }
}

test('auth changes notify other tabs through BroadcastChannel', async () => {
  const original = globalThis.BroadcastChannel
  Object.defineProperty(globalThis, 'BroadcastChannel', { configurable: true, value: FakeBroadcastChannel })
  try {
    let notifications = 0
    const unsubscribe = subscribeAuthChanges(() => notifications++)
    publishAuthChange()
    await new Promise((resolve) => setTimeout(resolve, 0))
    unsubscribe()
    assert.equal(notifications, 1)
  } finally {
    Object.defineProperty(globalThis, 'BroadcastChannel', { configurable: true, value: original })
    FakeBroadcastChannel.channels.clear()
  }
})

test('auth changes use the storage event fallback when BroadcastChannel is unavailable', () => {
  const oldChannel = globalThis.BroadcastChannel
  const oldWindow = globalThis.window
  const oldStorage = globalThis.localStorage
  const handlers = new Set()
  Object.defineProperty(globalThis, 'BroadcastChannel', { configurable: true, value: undefined })
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      addEventListener: (_type, handler) => handlers.add(handler),
      removeEventListener: (_type, handler) => handlers.delete(handler),
    },
  })
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { setItem: (key: string) => handlers.forEach((handler) => handler({ key })) },
  })
  try {
    let notifications = 0
    const unsubscribe = subscribeAuthChanges(() => notifications++)
    publishAuthChange()
    unsubscribe()
    assert.equal(notifications, 1)
  } finally {
    Object.defineProperty(globalThis, 'BroadcastChannel', { configurable: true, value: oldChannel })
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: oldStorage })
  }
})
