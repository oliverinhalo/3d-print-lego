import { useMemo, useState } from 'react'
import type { Part, PartStatus } from '../types'

const STATUS_ICON: Record<PartStatus, string> = {
  queued: '○',
  downloading: '⟳',
  converting: '⟳',
  validating: '⟳',
  ready: '✓',
  cached: '✓',
  failed: '⚠',
}

const SPINNING: PartStatus[] = ['downloading', 'converting', 'validating']

/**
 * Parts appear here as they are discovered and change state as they are
 * processed. Long inventories are capped rather than rendered in full: a
 * 400-part set would otherwise repaint hundreds of rows on every event.
 */
export function PartsList({ parts }: { parts: Part[] }) {
  const [showAll, setShowAll] = useState(false)

  const sorted = useMemo(() => {
    // Finished work first, then what is happening now, then what is waiting.
    // Failures sit last: they are surfaced prominently in the summary panel,
    // and leading with red while the job is still succeeding reads as alarm.
    const rank: Record<PartStatus, number> = {
      ready: 0, cached: 0,
      converting: 1, downloading: 1, validating: 1,
      queued: 2, failed: 3,
    }
    return [...parts].sort((a, b) =>
      rank[a.status] - rank[b.status] ||
      b.quantity - a.quantity ||
      a.part_num.localeCompare(b.part_num))
  }, [parts])

  const LIMIT = 60
  const visible = showAll ? sorted : sorted.slice(0, LIMIT)
  const done = parts.filter(p => p.status === 'ready' || p.status === 'cached').length

  if (parts.length === 0) {
    return (
      <div className="card">
        <div className="parts-head"><h3>Parts</h3></div>
        <p className="center-note" style={{ padding: '26px 0' }}>
          Waiting for the inventory…
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="parts-head">
        <h3>Parts</h3>
        <span className="parts-count">{done} of {parts.length} ready</span>
      </div>

      <div className="parts-list">
        {visible.map((part) => (
          <div className="part-row" key={part.part_num}>
            <span className={`part-status ${part.status}`}>
              {SPINNING.includes(part.status)
                ? <span className="spin">{STATUS_ICON[part.status]}</span>
                : STATUS_ICON[part.status]}
            </span>
            {part.img_url
              ? <img className="part-thumb" src={part.img_url} alt="" loading="lazy"
                     onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
              : <span className="part-thumb placeholder" />}
            <span className="part-name" title={`${part.part_num} — ${part.name}`}>
              {part.name}
              {' '}<span className="part-id">{part.part_num}</span>
            </span>
            {part.status === 'cached'
              ? <span className="part-cached-badge">cached</span>
              : <span />}
            <span className="part-qty">×{part.quantity}</span>
          </div>
        ))}
      </div>

      {sorted.length > LIMIT && (
        <button className="details-toggle" onClick={() => setShowAll(v => !v)}>
          {showAll ? 'Show fewer' : `Show all ${sorted.length} parts`}
        </button>
      )}
    </div>
  )
}
