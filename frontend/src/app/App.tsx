import { FormEvent, useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { connectStream, createIndexTask, createSearchTask, fetchPublicConfig, fetchState, SourceItem } from '../lib/api'

type ParsedBlock =
  | { type: 'markdown'; content: string }
  | { type: 'table'; headers: string[]; rows: string[][] }

function isTableSeparator(line: string) {
  const normalized = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  if (!normalized || !normalized.includes('|')) return false
  return normalized.split('|').every((cell) => /^:?-{3,}:?$/.test(cell.trim()))
}

function parseTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function parseMarkdownBlocks(markdown: string): ParsedBlock[] {
  const lines = markdown.split('\n')
  const blocks: ParsedBlock[] = []
  const markdownLines: string[] = []

  function flushMarkdown() {
    const chunk = markdownLines.join('\n').trim()
    if (chunk) {
      blocks.push({ type: 'markdown', content: chunk })
    }
    markdownLines.length = 0
  }

  for (let i = 0; i < lines.length; i += 1) {
    const current = lines[i]
    const next = lines[i + 1]
    const isTableHeader = current.includes('|') && typeof next === 'string' && isTableSeparator(next)

    if (!isTableHeader) {
      markdownLines.push(current)
      continue
    }

    flushMarkdown()

    const headers = parseTableRow(current)
    const rows: string[][] = []

    i += 2
    while (i < lines.length && lines[i].includes('|')) {
      rows.push(parseTableRow(lines[i]))
      i += 1
    }
    i -= 1

    blocks.push({ type: 'table', headers, rows })
  }

  flushMarkdown()
  return blocks
}

export function App() {
  const [query, setQuery] = useState('')
  const [currentStatus, setCurrentStatus] = useState('')
  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<SourceItem[]>([])
  const [isIndexing, setIsIndexing] = useState(false)
  const [isSearching, setIsSearching] = useState(false)
  const [indexReady, setIndexReady] = useState(false)
  const [error, setError] = useState('')
  const [sourceUrl, setSourceUrl] = useState('URL для парсинга (по умолчанию берется из переменной окружения)')
  const [indexProgress, setIndexProgress] = useState(0)

  useEffect(() => {
    fetchState()
      .then((s) => {
        setIsIndexing(s.indexing_running)
        setIndexReady(s.index_ready)
      })
      .catch(() => setError('Не удалось получить состояние сервиса'))
    fetchPublicConfig()
      .then((cfg) => {
        if (cfg.src_base_url) setSourceUrl(cfg.src_base_url)
      })
      .catch(() => undefined)
  }, [])

  const canSearch = useMemo(() => indexReady && !isIndexing && !isSearching, [indexReady, isIndexing, isSearching])
  const outputMarkdown = useMemo(() => {
    if (!indexReady) {
      return ''
    }
    if (!answer) {
      return currentStatus || 'Ожидание действия'
    }
    if (!sources.length) {
      return answer
    }
    const uniq: SourceItem[] = []
    const seen = new Set<string>()
    for (const source of sources) {
      const normalizedTitle = source.title.toLowerCase().replace(/\W+/g, ' ').trim().replace(/\s+/g, ' ')
      const key = `${source.url}|${normalizedTitle}`
      if (seen.has(key)) continue
      seen.add(key)
      uniq.push(source)
    }
    const numberedSources = uniq.map((item, idx) => `${idx + 1}. [${item.title}](${item.url})`)
    return `${answer}\n\nИсточники:\n${numberedSources.join('\n')}`
  }, [answer, currentStatus, indexReady, sources])

  const outputBlocks = useMemo(() => parseMarkdownBlocks(outputMarkdown), [outputMarkdown])

  async function handleIndex() {
    setError('')
    setCurrentStatus('')
    setIsIndexing(true)
    setIndexProgress(0)
    setIndexReady(false)
    try {
      const taskId = await createIndexTask(sourceUrl.trim())
      connectStream(`/api/index/stream?task_id=${taskId}`, {
        status: (p) => {
          setCurrentStatus(p.message ?? '')
          if (typeof p.progress === 'number') {
            setIndexProgress(Math.max(0, Math.min(100, p.progress)))
          }
        },
        done: () => {
          setIsIndexing(false)
          setIndexProgress(100)
          setIndexReady(true)
          setCurrentStatus('')
        },
        error: (p) => {
          setError(p.detail ?? 'Ошибка индексации')
          setIsIndexing(false)
          setIndexProgress(0)
          setCurrentStatus('')
        },
      })
    } catch (e) {
      setIsIndexing(false)
      setIndexProgress(0)
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
        <h1>Интеллектуальная система поиска информации в корпоративной документации</h1>
        <div className="actions panel">
          <div className="search-row">
            <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} />
            <button className="action-btn btn-index" onClick={handleIndex} disabled={isIndexing}>
              {isIndexing ? `Индексация ${indexProgress}%` : 'Индексация'}
            </button>
          </div>
          <form className="search-row" onSubmit={handleSearch}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={isIndexing ? 'Поиск станет доступен после успешной индексации.' : 'Введите запрос'}
              disabled={isIndexing}
            />
            <button className="action-btn btn-search" type="submit" disabled={!canSearch || !query.trim()}>
              {isSearching ? 'Поиск...' : 'Поиск'}
            </button>
          </form>
        </div>

        {error && <div className="error">{error}</div>}

        {outputMarkdown && (
          <section className="panel answer">
            {outputBlocks.map((block, idx) =>
              block.type === 'markdown' ? (
                <ReactMarkdown key={`md-${idx}`}>{block.content}</ReactMarkdown>
              ) : (
                <div className="answer-table-wrap" key={`tbl-${idx}`}>
                  <table>
                    <thead>
                      <tr>
                        {block.headers.map((header, headerIdx) => (
                          <th key={headerIdx}>
                            <ReactMarkdown>{header}</ReactMarkdown>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {block.rows.map((row, rowIdx) => (
                        <tr key={rowIdx}>
                          {row.map((cell, cellIdx) => (
                            <td key={cellIdx}>
                              <ReactMarkdown>{cell}</ReactMarkdown>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ),
            )}
          </section>
        )}
      </section>
    </main>
  )
}
