import { useEffect, useMemo, useRef, useState } from 'react'
import { browseSets, getThemes } from '../lib/api'
import type { LegoSet } from '../types'

interface Props {
  onPick: (set: LegoSet) => void
  onClose: () => void
}

const SORTS = [
  { id: 'popular', label: 'Biggest' },
  { id: 'newest', label: 'Newest' },
  { id: 'smallest', label: 'Smallest' },
  { id: 'name', label: 'A–Z' },
]

const PAGE = 24

/**
 * Browse the catalogue and click a set to build it — no copying numbers.
 *
 * This reads the local dataset rather than embedding lego.com, which is not
 * possible in any case: lego.com sends `X-Frame-Options: SAMEORIGIN`, so a
 * browser refuses to render it in a frame. Serving it locally is also
 * instant and works offline.
 */
export function Browse({ onPick, onClose }: Props) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [theme, setTheme] = useState<number | undefined>()
  const [sort, setSort] = useState('popular')
  const [themes, setThemes] = useState<{ id: number; name: string; sets: number }[]>([])
  const [sets, setSets] = useState<LegoSet[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const requestId = useRef(0)

  useEffect(() => {
    getThemes().then(r => setThemes(r.themes)).catch(() => { /* filters are optional */ })
  }, [])

  // Typing should not fire a request per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => { setOffset(0) }, [debounced, theme, sort])

  useEffect(() => {
    const id = ++requestId.current
    setLoading(true)
    setError('')
    browseSets({ q: debounced, theme, sort, limit: PAGE, offset })
      .then(result => {
        if (id !== requestId.current) return      // a newer query won
        setSets(result.results)
        setTotal(result.total)
      })
      .catch(err => { if (id === requestId.current) setError((err as Error).message) })
      .finally(() => { if (id === requestId.current) setLoading(false) })
  }, [debounced, theme, sort, offset])

  const pages = Math.ceil(total / PAGE)
  const page = Math.floor(offset / PAGE) + 1
  const heading = useMemo(() => {
    if (debounced) return `${total.toLocaleString()} sets matching “${debounced}”`
    if (theme) return `${total.toLocaleString()} sets in ${themes.find(t => t.id === theme)?.name ?? 'theme'}`
    return `${total.toLocaleString()} sets`
  }, [debounced, theme, total, themes])

  return (
    <main className="browse">
      <div className="browse-head">
        <div>
          <h1>Choose a set</h1>
          <p className="browse-count">{loading ? 'Searching…' : heading}</p>
        </div>
        <button className="btn-ghost" onClick={onClose}>Enter a number instead</button>
      </div>

      <div className="browse-filters">
        <input
          className="browse-search"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search by name or number…"
          aria-label="Search sets"
          autoFocus
        />
        <select className="select" value={theme ?? ''} aria-label="Theme"
                onChange={e => setTheme(e.target.value ? Number(e.target.value) : undefined)}>
          <option value="">All themes</option>
          {themes.map(t => (
            <option key={t.id} value={t.id}>{t.name} ({t.sets})</option>
          ))}
        </select>
        <div className="sort-group">
          {SORTS.map(s => (
            <button key={s.id} className={`sort-btn${sort === s.id ? ' on' : ''}`}
                    onClick={() => setSort(s.id)}>{s.label}</button>
          ))}
        </div>
      </div>

      {error && <div className="notice error"><span className="icon">⚠</span><div>{error}</div></div>}

      {!error && sets.length === 0 && !loading && (
        <p className="center-note">No sets match that. Try a different search.</p>
      )}

      <div className={`set-grid${loading ? ' loading' : ''}`}>
        {sets.map(set => (
          <button className="set-card" key={set.set_num} onClick={() => onPick(set)}>
            <div className="set-card-img">
              {set.img_url
                ? <img src={set.img_url} alt="" loading="lazy"
                       onError={e => { e.currentTarget.style.visibility = 'hidden' }} />
                : <span className="brick-glyph" />}
            </div>
            <div className="set-card-body">
              <strong title={set.name}>{set.name}</strong>
              <span className="set-card-meta">
                #{set.display_number} · {set.num_parts.toLocaleString()} pieces
                {set.year ? ` · ${set.year}` : ''}
              </span>
            </div>
            <span className="set-card-cta">Build this</span>
          </button>
        ))}
      </div>

      {pages > 1 && (
        <div className="pager">
          <button className="btn-ghost" disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span className="pager-info">Page {page} of {pages.toLocaleString()}</span>
          <button className="btn-ghost" disabled={page >= pages}
                  onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      )}
    </main>
  )
}
