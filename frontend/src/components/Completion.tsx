import { useState } from 'react'
import { downloadUrl } from '../lib/api'
import type { JobSummary, Part } from '../types'

interface Props {
  job: JobSummary
  failedParts: Part[]
  onRetry: () => void
  onStartOver: () => void
  retrying: boolean
}

function formatBytes(bytes: number): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++ }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

export function Completion({ job, failedParts, onRetry, onStartOver, retrying }: Props) {
  const [showFailed, setShowFailed] = useState(false)
  const partial = job.status === 'partial' && failedParts.length > 0
  const missingPieces = failedParts.reduce((sum, p) => sum + p.quantity, 0)

  return (
    <div className="card done-card">
      <div className="done-mark">✓</div>
      <h2>Your set is ready</h2>
      <p className="sub">
        {job.total_pieces} printable {job.total_pieces === 1 ? 'piece' : 'pieces'}
        {job.plate_count > 0 && <> · {job.plate_count} build {job.plate_count === 1 ? 'plate' : 'plates'}</>}
        {' · '}{job.distinct_geometries} unique models
      </p>

      <a className="btn-download" href={downloadUrl(job.job_id)} download>
        <span>↓</span>
        {partial ? 'Download available parts' : 'Download ZIP'}
      </a>
      <div className="zip-meta">
        {job.zip_name} · {formatBytes(job.zip_bytes)}
      </div>

      {job.oversized?.length > 0 && (
        <div className="notice warn" style={{ marginTop: 18, textAlign: 'left' }}>
          <span className="icon">⚠</span>
          <div>
            <strong>{job.oversized.length} part(s) are too big for this printer.</strong>
            They were left out: {job.oversized.join(', ')}. Try a larger printer.
          </div>
        </div>
      )}

      {partial && (
        <div style={{ marginTop: 22, textAlign: 'left' }}>
          <div className="notice warn">
            <span className="icon">⚠</span>
            <div style={{ minWidth: 0 }}>
              <strong>
                {failedParts.length} part{failedParts.length === 1 ? '' : 's'} could not
                be generated ({missingPieces} piece{missingPieces === 1 ? '' : 's'}).
              </strong>
              Everything else is in the download.
              <div style={{ marginTop: 6 }}>
                <button className="details-toggle" onClick={() => setShowFailed(v => !v)}>
                  {showFailed ? 'Hide details' : 'View problem'}
                </button>
              </div>
            </div>
          </div>

          {showFailed && (
            <div className="failed-list">
              {failedParts.map(part => (
                <div className="failed-item" key={part.part_num}>
                  <span className="fid">{part.part_num}</span>
                  <span className="fname" title={part.error ?? undefined}>
                    {part.name} — {part.error}
                  </span>
                  <span className="fqty">×{part.quantity}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {job.plates?.length > 0 && (
        <div className="plates-panel">
          <h4>Build plates</h4>
          <p className="plates-hint">
            Open <strong>one plate at a time</strong>. Each is already arranged —
            no importing hundreds of files, no Auto Arrange.
          </p>
          <div className="plate-grid">
            {job.plates.map(plate => (
              <div className="plate-chip" key={plate.index}>
                <span className="plate-num">{plate.index}</span>
                <span className="plate-meta">
                  <strong>{plate.group || 'Any colour'}</strong>
                  <span>{plate.count} pieces · {plate.fill_percent}% full</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="next-steps">
        <h4>Next steps</h4>
        <ol>
          <li>Extract the ZIP and open the <code>Plates</code> folder.</li>
          <li>Open <strong>one</strong> plate file, e.g. <code>Plate_01.3mf</code>.</li>
          <li>It opens already arranged — slice and print.</li>
          <li>Repeat for each plate.</li>
        </ol>
      </div>

      <div className="actions">
        {job.oversized?.length > 0 && (
        <div className="notice warn" style={{ marginTop: 18, textAlign: 'left' }}>
          <span className="icon">⚠</span>
          <div>
            <strong>{job.oversized.length} part(s) are too big for this printer.</strong>
            They were left out: {job.oversized.join(', ')}. Try a larger printer.
          </div>
        </div>
      )}

      {partial && (
          <button className="btn-ghost" onClick={onRetry} disabled={retrying}>
            {retrying ? 'Retrying…' : 'Retry failed parts'}
          </button>
        )}
        <button className="btn-ghost" onClick={onStartOver}>Generate another set</button>
      </div>
    </div>
  )
}
