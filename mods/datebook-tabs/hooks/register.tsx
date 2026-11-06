import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ThreadTab } from '../types'

const PANE = 'datebook-tabs'
const SERVER = 'hearthbot'
const REFRESH_MS = 60_000
const DEFAULT_PROJECT = 'chan_013NsRYLYEJxEaSqhN4pyUUz'

const tabs = atom({ plugin: 'datebook-tabs', key: 'tabs' } as const, [], { shape: 'tabs-1' })
const projectId = atom({ plugin: 'datebook-tabs', key: 'projectId' } as const, '', { shape: 'pid-1' })
const error = atom({ plugin: 'datebook-tabs', key: 'error' } as const, '', { shape: 'err-1' })

// Paper-planner tab colours, cycled down the edge.
const COLORS = ['#F4C95D', '#E8875F', '#9CC69B', '#7FB7D9', '#C9A3D9', '#F2A7B5', '#B7C97F']

const STATE_MARK: Record<string, string> = {
  working: '✎',
  blocked: '?',
  review_ready: '★',
  done: '✓',
  failed: '!',
  stopped: '■',
}

type RawSession = {
  thread_id?: string
  title?: string
  state?: string
  last_activity_at?: string
}

function parseSessions(text: string): RawSession[] {
  const start = text.indexOf('{"sessions"')
  if (start < 0) return []
  const end = text.lastIndexOf('}')
  try {
    const parsed = JSON.parse(text.slice(start, end + 1)) as { sessions?: RawSession[] }
    return parsed.sessions ?? []
  } catch {
    return []
  }
}

// A thread id (cmsg_<22 chars of the project's id><...>) carries its project's id.
function projectFromThread(threadId: string): string {
  const core = threadId.replace(/^cmsg_/, '').slice(0, 22)
  return core.length === 22 ? `chan_${core}` : DEFAULT_PROJECT
}

function shortDate(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[d.getMonth()]} ${d.getDate()}`
}

async function refresh($: EngineInterface) {
  try {
    const all: RawSession[] = []
    let cursor: string | undefined
    for (let page = 0; page < 5; page++) {
      const res = await $.mcp.call(SERVER, 'list_threads', cursor ? { limit: 50, cursor } : { limit: 50 })
      const text = (res.content ?? [])
        .map(block => (block.type === 'text' ? block.text : ''))
        .join('\n')
      all.push(...parseSessions(text))
      const next = /"next_cursor"\s*:\s*"([^"]+)"/.exec(text)
      if (!next) break
      cursor = next[1]
    }
    const list: ThreadTab[] = all
      .filter(s => s.thread_id)
      .map(s => ({
        id: s.thread_id as string,
        title: s.title || 'Untitled thread',
        state: s.state ?? '',
        lastActivity: s.last_activity_at ?? '',
      }))
      .sort((a, b) => b.lastActivity.localeCompare(a.lastActivity))
    await update($, tabs, () => list)
    await update($, error, () => '')
  } catch (err) {
    await update($, error, () => `Could not list threads: ${String(err)}`)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.command.register({
      name: 'datebook',
      description: 'Show project threads as date-book tabs on the right',
    })
    void $.ui.open({ id: PANE, title: 'Threads' })
    void refresh($)
    $.clock.every(REFRESH_MS, () => void refresh($))
    return result
  })

  on('command.run', { command: 'datebook' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Threads', focus: true })
    await refresh($)
    return { text: 'Date-book tabs opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Link, Button } = $.ui.resolve(e)
    const list = await read($, tabs)
    const err = await read($, error)
    const pid = await read($, projectId)
    const room = Math.max(1, Math.floor(((e.viewport?.rows ?? 30) - 3) / 3))

    return (
      <Box flexDirection="column" alignItems="flex-end" gap={0}>
        {err && <Text color="error">{err}</Text>}
        {!err && list.length === 0 && <Text dimColor>No threads yet.</Text>}
        {list.slice(0, room).map((t, i) => {
          const color = COLORS[i % COLORS.length]
          const href = `https://claude.ai/code/project/${pid || DEFAULT_PROJECT}?thread=${t.id}`
          return (
            <Box
              key={t.id}
              flexDirection="row"
              justifyContent="space-between"
              width="100%"
              backgroundColor={color}
              borderStyle="round"
              borderColor={color}
              paddingX={1}
              hover={{ backgroundColor: '#FFF6DA' }}
            >
              <Link href={href} label={t.title} />
              <Text color="#3A3226">
                {STATE_MARK[t.state] ?? ''} {shortDate(t.lastActivity)}
              </Text>
            </Box>
          )
        })}
        <Button key="refresh" label="↻ Refresh" onPress={() => void refresh($)} />
      </Box>
    )
  })
}
