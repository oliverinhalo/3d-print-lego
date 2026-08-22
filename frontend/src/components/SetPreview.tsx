import { useEffect, useState } from 'react'
import type { JobSummary, LegoSet } from '../types'

interface Props {
  set: LegoSet | null
  job: JobSummary | null
  estimatedFiles: number
}

/**
 * Left column: the set itself. Appears as soon as the set is identified,
 * long before generation finishes, so there is always something real on
 * screen while the pipeline works.
 */
export function SetPreview({ set, job, estimatedFiles }: Props) {
  // The set photo is hosted by the catalogue, so it can fail on a locked-down
  // network. Fall back to a drawn placeholder rather than an empty frame.
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => { setImageFailed(false) }, [set?.img_url])
  const showImage = Boolean(set?.img_url) && !imageFailed

  return (
    <aside className="card set-preview">
      <div className={`set-image${set ? '' : ' skeleton'}`}>
        {showImage ? (
          <img src={set!.img_url!} alt={set!.name} loading="eager"
               onError={() => setImageFailed(true)} />
        ) : set ? (
          <div className="set-image-fallback">
            <span className="brick-glyph" aria-hidden="true" />
            <span>#{set.display_number}</span>
          </div>
        ) : null}
      </div>

      <div className="set-number">#{set?.display_number ?? job?.query ?? '—'}</div>
      <h1 className="set-name">{set?.name ?? 'Finding your set…'}</h1>
      {set && (
        <div className="set-theme">
          {[set.theme, set.year].filter(Boolean).join(' · ')}
        </div>
      )}

      <div className="stat-grid">
        <div className="stat">
          <div className="value">{job?.total_pieces || set?.num_parts || '—'}</div>
          <div className="label">Pieces</div>
        </div>
        <div className="stat">
          <div className="value">{job?.unique_parts || '—'}</div>
          <div className="label">Unique</div>
        </div>
        <div className="stat">
          <div className="value">{estimatedFiles || '—'}</div>
          <div className="label">STL files</div>
        </div>
      </div>
    </aside>
  )
}
