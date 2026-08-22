import type { JobSummary } from '../types'

const STAGES = [
  { key: 'finding_set', label: 'Finding LEGO set' },
  { key: 'loading_inventory', label: 'Loading inventory' },
  { key: 'identifying_parts', label: 'Identifying parts' },
  { key: 'finding_models', label: 'Finding 3D models' },
  { key: 'converting', label: 'Converting geometry' },
  { key: 'duplicating', label: 'Creating duplicate parts' },
  { key: 'building_zip', label: 'Building ZIP' },
  { key: 'complete', label: 'Complete' },
]

interface Props {
  job: JobSummary | null
  progress: number
  currentAction: string
}

export function ProgressPanel({ job, progress, currentAction }: Props) {
  const stageIndex = Math.max(0, STAGES.findIndex(s => s.key === job?.stage))
  const running = job?.status === 'running' || job?.status === 'pending'
  const pct = Math.round(Math.min(100, Math.max(0, progress)))

  return (
    <div className="card">
      <div className="status-head">
        <div className="status-title">
          {running ? 'Building your printable set' : 'Generation finished'}
        </div>
        <div className="status-pct">{pct}%</div>
      </div>

      <div className="progress-track">
        <div className={`progress-fill${running ? ' active' : ''}`}
             style={{ width: `${pct}%` }} />
      </div>

      <div className="current-action">
        {running && <span className="dot" />}
        <span>{currentAction || job?.stage_label || 'Starting…'}</span>
      </div>

      <div className="stages">
        {STAGES.map((stage, index) => {
          const state = index < stageIndex ? 'done'
            : index === stageIndex ? (job?.status === 'running' ? 'active' : 'done')
            : ''
          return (
            <div className={`stage-row ${state}`} key={stage.key}>
              <span className="stage-icon">
                {state === 'done' ? '✓' : state === 'active' ? '●' : '○'}
              </span>
              <span>{stage.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
