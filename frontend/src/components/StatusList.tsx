type Props = { title: string; items: string[] }

export function StatusList({ title, items }: Props) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted">Пока пусто</p>
      ) : (
        <ul className="status-list">
          {items.map((item, idx) => (
            <li key={`${item}-${idx}`}>{item}</li>
          ))}
        </ul>
      )}
    </section>
  )
}
