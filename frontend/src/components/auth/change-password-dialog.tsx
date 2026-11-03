import { useMutation } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import { publishAuthChange } from '@/lib/auth-events'

export function ChangePasswordDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => {
      publishAuthChange()
      toast.success('Password changed. Your other devices were signed out.')
      setCurrent('')
      setNext('')
      setAgain('')
      onOpenChange(false)
    },
  })
  const mismatch = again !== '' && next !== again
  const valid = current && next.length >= 10 && next === again

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            change.mutate()
          }}
          className="space-y-4"
        >
          <DialogHeader>
            <DialogTitle>Change password</DialogTitle>
            <DialogDescription>At least 10 characters. Changing it signs you out everywhere else.</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="cur">Current password</Label>
            <Input id="cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new">New password</Label>
            <Input id="new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="again">Repeat new password</Label>
            <Input id="again" type="password" autoComplete="new-password" aria-invalid={mismatch} value={again} onChange={(e) => setAgain(e.target.value)} />
            {mismatch && <p className="text-xs text-destructive">Passwords don't match</p>}
          </div>
          {change.error && <p className="text-sm text-destructive">{change.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || change.isPending}>
              {change.isPending && <Loader2 className="animate-spin" />} Change password
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
