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
        {job.files_written} printable {job.files_written === 1 ? 'piece' : 'pieces'}
        {' · '}{job.distinct_geometries} unique models
      </p>

      <a className="btn-download" href={downloadUrl(job.job_id)} download>
        <span>↓</span>
        {partial ? 'Download available parts' : 'Download ZIP'}
      </a>
      <div className="zip-meta">
        {job.zip_name} · {formatBytes(job.zip_bytes)}
      </div>

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

      <div className="next-steps">
        <h4>Next steps</h4>
        <ol>
          <li>Extract the ZIP and open the <code>STLs</code> folder.</li>
          <li>Select every file — <code>Ctrl</code>+<code>A</code> or <code>⌘</code>+<code>A</code>.</li>
          <li>Drag them into Bambu Studio, OrcaSlicer, Cura or PrusaSlicer.</li>
          <li>Run the slicer's <strong>Auto Arrange</strong> and print.</li>
        </ol>
      </div>

      <div className="actions">
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
