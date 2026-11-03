import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Database, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { ThemeToggle } from '@/components/theme-toggle'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import { publishAuthChange } from '@/lib/auth-events'

export function LoginScreen() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: () => api.login(email.trim(), password),
    onSuccess: (user) => {
      qc.setQueryData(['me'], user)
      publishAuthChange()
    },
  })

  return (
    <div className="relative grid min-h-dvh place-items-center px-4">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          login.mutate()
        }}
        className="w-full max-w-sm space-y-5"
      >
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="grid size-12 place-items-center rounded-2xl bg-primary text-primary-foreground">
            <Database className="size-6" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Sign in to pgweb2</h1>
            <p className="text-sm text-muted-foreground">Use the account your admin created for you.</p>
          </div>
        </div>
        <div className="space-y-4 rounded-xl border bg-card p-5">
          <div className="space-y-1.5">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {login.error && (
            <p role="alert" className="text-sm text-destructive">
              {login.error.message}
            </p>
          )}
          <Button type="submit" className="w-full" disabled={!email || !password || login.isPending}>
            {login.isPending && <Loader2 className="animate-spin" />} Sign in
          </Button>
        </div>
      </form>
    </div>
  )
}
