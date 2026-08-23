import { useState } from 'react'
import type { Estimate } from '../types'

/**
 * Filament, cost and time for the whole set.
 *
 * Filament is derived from real mesh volume, so it is fairly trustworthy.
 * Time depends on speeds and travel that only a slicer knows, so it is
 * labelled as rough rather than presented with false precision.
 */
export function EstimatePanel({ estimate }: { estimate: Estimate }) {
  const [showDetail, setShowDetail] = useState(false)
  const money = `${estimate.currency}${estimate.cost.toFixed(2)}`
  const spools = estimate.kilograms

  return (
    <div className="estimate">
      <div className="estimate-row">
        <div className="estimate-figure">
          <span className="estimate-value">{estimate.grams.toFixed(0)}<small>g</small></span>
          <span className="estimate-label">Filament</span>
        </div>
        <div className="estimate-figure">
          <span className="estimate-value">{money}</span>
          <span className="estimate-label">Material cost</span>
        </div>
        <div className="estimate-figure">
          <span className="estimate-value">{estimate.time_text}</span>
          <span className="estimate-label">Print time <em>(rough)</em></span>
        </div>
      </div>

      <button className="details-toggle" onClick={() => setShowDetail(v => !v)}>
        {showDetail ? 'Hide assumptions' : 'How is this worked out?'}
      </button>

      {showDetail && (
        <div className="estimate-detail">
          <p>
            Filament comes from each part's real volume, split into perimeter
            and infill using its own wall thickness — so it should be close.
            Time is a rough model of flow rate and travel; your slicer is the
            authority and may differ by a good margin.
          </p>
          <dl>
            <div><dt>Filament length</dt><dd>{estimate.metres.toFixed(0)} m</dd></div>
            <div><dt>Spools (1 kg)</dt><dd>{spools < 0.1 ? '<0.1' : spools.toFixed(2)}</dd></div>
            <div><dt>Layer height</dt><dd>{estimate.profile.layer_height_mm} mm</dd></div>
            <div><dt>Walls</dt><dd>{estimate.profile.wall_count}</dd></div>
            <div><dt>Infill</dt><dd>{estimate.profile.infill_percent}%</dd></div>
            <div><dt>Filament price</dt>
              <dd>{estimate.currency}{estimate.profile.price_per_kg}/kg</dd></div>
          </dl>
          <p className="estimate-note">
            These assumptions are configurable on the server, so the figures
            can be made to track your own profile and filament price.
          </p>
        </div>
      )}
    </div>
  )
}
