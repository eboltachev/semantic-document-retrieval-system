import { FormEvent, useEffect, useMemo, useState } from 'react'
import { connectStream, createIndexTask, createSearchTask, fetchState, SourceItem } from '../lib/api'

export function App() {
  const [query, setQuery] = useState('')
  const [currentStatus, setCurrentStatus] = useState('')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<SourceItem[]>([])
  const [isIndexing, setIsIndexing] = useState(false)
  const [isSearching, setIsSearching] = useState(false)
  const [indexReady, setIndexReady] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchState()
      .then((s) => {
        setIsIndexing(s.indexing_running)
        setIndexReady(s.index_ready)
      })
      .catch(() => setError('Не удалось получить состояние сервиса'))
  }, [])

  const canSearch = useMemo(() => indexReady && !isIndexing && !isSearching, [indexReady, isIndexing, isSearching])
  const answerWithSources = useMemo(() => {
    if (!answer) return 'Ответ появится здесь в потоковом режиме'
    if (!sources.length) return answer
    const sourceLines = sources.map((item) => `- [${item.title}](${item.url}) — ${item.preview}`)
    return `${answer}\n\nИсточники:\n${sourceLines.join('\n')}`
  }, [answer, sources])

  async function handleIndex() {
    setError('')
    setCurrentStatus('')
    setIsIndexing(true)
    setIndexReady(false)
    try {
      const taskId = await createIndexTask()
      connectStream(`/api/index/stream?task_id=${taskId}`, {
        status: (p) => setCurrentStatus(p.message ?? ''),
        done: () => {
          setIsIndexing(false)
          setIndexReady(true)
          setCurrentStatus('')
        },
        error: (p) => {
          setError(p.detail ?? 'Ошибка индексации')
          setIsIndexing(false)
          setCurrentStatus('')
        },
      })
    } catch (e) {
      setIsIndexing(false)
      setCurrentStatus('')
      setError((e as Error).message)
    }
  }

  async function handleSearch(e?: FormEvent) {
    e?.preventDefault()
    const value = query.trim()
    if (!value) return
    setError('')
    setCurrentStatus('')
    setAnswer('')
    setSources([])
    setIsSearching(true)
    try {
      const taskId = await createSearchTask(value)
      connectStream(`/api/search/stream?task_id=${taskId}`, {
        status: (p) => setCurrentStatus(p.message ?? ''),
        token: (p) => setAnswer((prev) => prev + p.text),
        sources: (p) => setSources(p.items ?? []),
        done: () => {
          setIsSearching(false)
          setCurrentStatus('')
        },
        error: (p) => {
          setError(p.detail ?? 'Ошибка поиска')
          setIsSearching(false)
          setCurrentStatus('')
        },
      })
    } catch (e) {
      setIsSearching(false)
      setCurrentStatus('')
      setError((e as Error).message)
    }
  }

  return (
    <main className="page">
      <section className="container">
        <h1>Semantic Docs Search</h1>
        <div className="actions panel">
          <button onClick={handleIndex} disabled={isIndexing}>
            {isIndexing ? 'Индексация...' : 'Индексация'}
          </button>
          <form className="search-row" onSubmit={handleSearch}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Введите запрос по документации"
            />
            <button type="submit" disabled={!canSearch || !query.trim()}>
              {isSearching ? 'Поиск...' : 'Поиск'}
            </button>
          </form>
          {!indexReady && <p className="muted">Поиск станет доступен после успешной индексации.</p>}
        </div>

        {error && <div className="error">{error}</div>}

        <section className="panel answer">
          <h3>Текущий вывод</h3>
          <p>{currentStatus || 'Ожидание действия'}</p>
        </section>

        <section className="panel answer">
          <h3>Ответ</h3>
          <p>{answerWithSources}</p>
        </section>
      </section>
    </main>
  )
}
