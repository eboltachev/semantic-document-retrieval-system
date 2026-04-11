import type { SourceItem } from '../lib/api'

type Props = { items: SourceItem[] }

export function Sources({ items }: Props) {
  if (!items.length) return null
  return (
    <section className="panel">
      <h3>Источники</h3>
      <div className="sources">
        {items.map((source) => (
          <a key={source.url} href={source.url} target="_blank" rel="noreferrer" className="source-card">
            <div className="source-title">{source.title}</div>
            <div className="source-url">{source.url}</div>
            <p>{source.preview}</p>
          </a>
        ))}
      </div>
    </section>
  )
}
