export type StreamEvent = 'status' | 'token' | 'sources' | 'done' | 'error'

export interface SourceItem {
  title: string
  url: string
  preview: string
}

export async function createIndexTask(): Promise<string> {
  const res = await fetch('/api/index/rebuild', { method: 'POST' })
  if (!res.ok) {
    const body = await res.json()
    throw new Error(body.detail ?? 'Не удалось запустить индексацию')
  }
  const body = await res.json()
  return body.task_id
}

export async function createSearchTask(query: string): Promise<string> {
  const res = await fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query }),
  })
  if (!res.ok) {
    const body = await res.json()
    throw new Error(body.detail ?? 'Не удалось запустить поиск')
  }
  const body = await res.json()
  return body.task_id
}

export async function fetchState(): Promise<{ indexing_running: boolean; index_ready: boolean }> {
  const res = await fetch('/api/state')
  if (!res.ok) throw new Error('Не удалось получить состояние')
  return res.json()
}

export async function fetchPublicConfig(): Promise<{ src_base_url?: string }> {
  const res = await fetch('/api/config/public')
  if (!res.ok) throw new Error('Не удалось получить публичную конфигурацию')
  return res.json()
}

export function connectStream(
  path: string,
  handlers: Partial<Record<StreamEvent, (payload: any) => void>>,
): () => void {
  const stream = new EventSource(path)
  let isTerminal = false
  ;(['status', 'token', 'sources', 'done', 'error'] as StreamEvent[]).forEach((eventName) => {
    stream.addEventListener(eventName, (event) => {
      const payload = JSON.parse((event as MessageEvent).data)
      handlers[eventName]?.(payload)
      if (eventName === 'done' || eventName === 'error') {
        isTerminal = true
        stream.close()
      }
    })
  })

  stream.onerror = () => {
    if (isTerminal || stream.readyState === EventSource.CLOSED) {
      return
    }
    handlers.error?.({ detail: 'Соединение SSE прервано' })
    isTerminal = true
    stream.close()
  }

  return () => {
    isTerminal = true
    stream.close()
  }
}
