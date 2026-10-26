// Admin-only login management: create a member's login, reset a password, remove a login.
// Usernames map to placeholder emails, since Supabase Auth signs in with email + password.
import { createClient } from 'npm:@supabase/supabase-js@2.45.4'

const EMAIL_DOMAIN = 'members.midaskiyoshi.fund'
const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
}
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } })

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors })
  if (req.method !== 'POST') return reply(405, { error: 'POST only' })

  const url = Deno.env.get('SUPABASE_URL')!
  const caller = createClient(url, Deno.env.get('SUPABASE_ANON_KEY')!, {
    global: { headers: { Authorization: req.headers.get('Authorization') ?? '' } },
  })
  const { data: isAdmin } = await caller.rpc('is_admin')
  if (isAdmin !== true) return reply(403, { error: 'Admins only' })
  const { data: me } = await caller.auth.getUser()

  const db = createClient(url, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!)
  let body: any
  try { body = await req.json() } catch { return reply(400, { error: 'Invalid JSON' }) }
  const { action, member_id, password } = body ?? {}
  const username = String(body?.username ?? '').trim().toLowerCase()

  const { data: member } = await db.from('members').select('*').eq('id', member_id).maybeSingle()
  if (!member) return reply(404, { error: 'Member not found' })
  const checkPassword = () => typeof password === 'string' && password.length >= 8

  if (action === 'create_login') {
    if (member.user_id) return reply(400, { error: `${member.name} already has a login` })
    if (!/^[a-z0-9._-]{3,30}$/.test(username)) return reply(400, { error: 'Username: 3–30 letters, numbers, dots, dashes or underscores' })
    if (!checkPassword()) return reply(400, { error: 'Password must be at least 8 characters' })
    const { data: taken } = await db.from('members').select('id').eq('username', username).maybeSingle()
    if (taken) return reply(400, { error: 'That username is taken' })

    const { data: created, error } = await db.auth.admin.createUser({
      email: `${username}@${EMAIL_DOMAIN}`, password, email_confirm: true, user_metadata: { username },
    })
    if (error || !created.user) return reply(400, { error: error?.message ?? 'Could not create login' })
    const { error: linkError } = await db.from('members')
      .update({ user_id: created.user.id, username }).eq('id', member.id).is('user_id', null)
    if (linkError) {
      await db.auth.admin.deleteUser(created.user.id)
      return reply(400, { error: linkError.message })
    }
    return reply(200, { ok: true, username })
  }

  if (action === 'reset_password') {
    if (!member.user_id) return reply(400, { error: `${member.name} has no login yet` })
    if (!checkPassword()) return reply(400, { error: 'Password must be at least 8 characters' })
    const { error } = await db.auth.admin.updateUserById(member.user_id, { password })
    if (error) return reply(400, { error: error.message })
    return reply(200, { ok: true })
  }

  if (action === 'remove_login') {
    if (!member.user_id) return reply(400, { error: `${member.name} has no login` })
    if (member.user_id === me?.user?.id) return reply(400, { error: "You can't remove your own login" })
    const { error } = await db.auth.admin.deleteUser(member.user_id)
    if (error) return reply(400, { error: error.message })
    await db.from('members').update({ user_id: null, username: null }).eq('id', member.id)
    return reply(200, { ok: true })
  }

  return reply(400, { error: 'Unknown action' })
})
