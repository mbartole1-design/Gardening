export type ThreadTab = {
  id: string
  title: string
  state: string
  lastActivity: string
}

declare module 'claude-code' {
  interface PluginState {
    'datebook-tabs': {
      tabs: Shaped<ThreadTab[]>
      projectId: Shaped<string>
      error: Shaped<string>
    }
  }
}
