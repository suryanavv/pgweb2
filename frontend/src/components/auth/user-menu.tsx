import { useQueryClient } from '@tanstack/react-query'
import { KeyRound, LogOut, ScrollText, Users } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { api } from '@/lib/api'
import type { User } from '@/lib/types'
import { useActive } from '@/stores/connections'
import { useWorkspace } from '@/stores/workspace'
import { publishAuthChange } from '@/lib/auth-events'
import { AuditDialog } from './audit-dialog'
import { ChangePasswordDialog } from './change-password-dialog'
import { UsersDialog } from './users-dialog'

export function UserMenu({ me }: { me: User }) {
  const qc = useQueryClient()
  const [dialog, setDialog] = useState<'password' | 'users' | 'audit' | null>(null)
  const initials = (me.name || me.email).slice(0, 2).toUpperCase()

  const signOut = async () => {
    await api.logout().catch(() => undefined)
    // Don't leave one person's cached data or SQL drafts in the browser for the next sign-in.
    useActive.getState().setActive(null)
    useWorkspace.setState({ byConn: {}, tableDrafts: {}, ownerUserId: null })
    qc.clear()
    qc.setQueryData(['me'], null)
    publishAuthChange()
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="Account menu" className="rounded-full" />}>
          <span className="grid size-7 place-items-center rounded-full bg-primary/15 text-[11px] font-semibold text-primary">{initials}</span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuGroup>
            <DropdownMenuLabel className="space-y-0.5">
              <div className="truncate text-sm font-medium text-foreground">{me.name || me.email}</div>
              <div className="truncate text-xs font-normal">{me.email} · {me.role}</div>
            </DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => setDialog('password')}>
            <KeyRound /> Change password
          </DropdownMenuItem>
          {me.role === 'admin' && (
            <>
              <DropdownMenuItem onClick={() => setDialog('users')}>
                <Users /> Team members
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setDialog('audit')}>
                <ScrollText /> Audit log
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={signOut}>
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangePasswordDialog open={dialog === 'password'} onOpenChange={(o) => !o && setDialog(null)} />
      {me.role === 'admin' && (
        <>
          <UsersDialog open={dialog === 'users'} onOpenChange={(o) => !o && setDialog(null)} me={me} />
          <AuditDialog open={dialog === 'audit'} onOpenChange={(o) => !o && setDialog(null)} />
        </>
      )}
    </>
  )
}
