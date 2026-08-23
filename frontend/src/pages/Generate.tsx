import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Completion } from '../components/Completion'
import { PartsList } from '../components/PartsList'
import { ProgressPanel } from '../components/ProgressPanel'
import { SetPreview } from '../components/SetPreview'
import { retryFailed, subscribe } from '../lib/api'
import type { JobEvent, JobSummary, LegoSet, Part } from '../types'

/**
 * Weighting of each stage in the overall progress bar. Conversion is the
 * only stage whose duration really varies, so it owns most of the range and
 * advances part-by-part; everything else is quick and fixed.
 */
const STAGE_FLOOR: Record<string, number> = {
  finding_set: 2,
  loading_inventory: 8,
  identifying_parts: 14,
  finding_models: 20,
  converting: 30,
  duplicating: 88,
  building_zip: 92,
  complete: 100,
}

interface Props {
  jobId: string
  onStartOver: () => void
}

export function Generate({ jobId, onStartOver }: Props) {
  const [job, setJob] = useState<JobSummary | null>(null)
  const [set, setSet] = useState<LegoSet | null>(null)
  const [parts, setParts] = useState<Map<string, Part>>(new Map())
  const [failedParts, setFailedParts] = useState<Part[]>([])
  const [progress, setProgress] = useState(0)
  const [action, setAction] = useState('Finding your set…')
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState(false)
  const unsubscribe = useRef<(() => void) | null>(null)

  const handleEvent = useCallback((event: JobEvent) => {
    switch (event.type) {
      case 'snapshot':
      case 'job_started':
        setJob(event.job)
        if (event.job.set) setSet(event.job.set)
        break

      case 'set_found':
        setSet(event.set)
        setAction(`Found ${event.set.name}`)
        break

      case 'stage':
        setAction(event.label)
        setProgress(p => Math.max(p, STAGE_FLOOR[event.stage] ?? p))
        setJob(j => j ? { ...j, stage: event.stage, stage_label: event.label } : j)
        break

      case 'inventory_loaded':
        setAction(`Loaded ${event.total_pieces} pieces`)
        break

      case 'parts_identified': {
        const next = new Map<string, Part>()
        for (const part of event.parts) next.set(part.part_num, part)
        setParts(next)
        setJob(j => j ? {
          ...j,
          unique_parts: event.unique_parts,
          total_pieces: event.total_pieces,
        } : j)
        setAction(`${event.unique_parts} unique parts found`)
        break
      }

      case 'models_resolved':
        setParts(prev => {
          const next = new Map(prev)
          for (const part of event.parts) next.set(part.part_num, part)
          return next
        })
        setJob(j => j ? { ...j, distinct_geometries: event.distinct_geometries } : j)
        setAction(`${event.distinct_geometries} unique shapes to prepare`)
        break

      case 'part_progress': {
        const part = event.part
        setParts(prev => {
          const next = new Map(prev)
          next.set(part.part_num, part)
          return next
        })
        if (event.total > 0) {
          const span = STAGE_FLOOR.duplicating - STAGE_FLOOR.converting
          setProgress(p => Math.max(
            p, STAGE_FLOOR.converting + (event.completed / event.total) * span))
        }
        if (part.status === 'converting') setAction(`Converting ${part.part_num} · ${part.name}`)
        break
      }

      case 'zip_progress':
        if (event.total > 0) {
          setProgress(p => Math.max(p, 92 + (event.written / event.total) * 7))
          setAction(`Writing ${event.written} of ${event.total} files`)
        }
        break

      case 'estimate_ready':
        setJob(j => j ? { ...j, estimate: event.estimate } : j)
        break

      case 'job_complete':
        setJob(event.job)
        if (event.job.set) setSet(event.job.set)
        setFailedParts(event.failed_parts ?? [])
        setProgress(100)
        setAction('Complete')
        setRetrying(false)
        break

      case 'job_failed':
        setError(event.error)
        if (event.job) setJob(event.job)
        setRetrying(false)
        break

      case 'job_cancelled':
        setJob(event.job)
        setError('Generation was cancelled.')
        break
    }
  }, [])

  useEffect(() => {
    setProgress(0)
    setError('')
    unsubscribe.current = subscribe(jobId, handleEvent, setError)
    return () => { unsubscribe.current?.() }
  }, [jobId, handleEvent])

  const partList = useMemo(() => Array.from(parts.values()), [parts])

  // Failed parts also come through part events, so the completion panel stays
  // accurate even if the terminal event was missed.
  const knownFailed = useMemo(() => {
    const byId = new Map(failedParts.map(p => [p.part_num, p]))
    for (const part of partList) if (part.status === 'failed') byId.set(part.part_num, part)
    return Array.from(byId.values())
  }, [failedParts, partList])

  const estimatedFiles = useMemo(() => {
    if (job?.files_written) return job.files_written
    return partList
      .filter(p => p.status !== 'failed')
      .reduce((sum, p) => sum + p.quantity, 0)
  }, [partList, job?.files_written])

  async function handleRetry() {
    setRetrying(true)
    setError('')
    try {
      await retryFailed(jobId)
      unsubscribe.current?.()
      unsubscribe.current = subscribe(jobId, handleEvent, setError)
    } catch (err) {
      setError((err as Error).message)
      setRetrying(false)
    }
  }

  const finished = job?.status === 'complete' || job?.status === 'partial'

  return (
    <main className="generate">
      <SetPreview set={set} job={job} estimatedFiles={estimatedFiles} />

      <div>
        {error && (
          <div className="card">
            <div className="notice error">
              <span className="icon">⚠</span>
              <div>
                <strong>Generation failed</strong>
                {error}
              </div>
            </div>
            <div className="actions">
              <button className="btn-ghost" onClick={onStartOver}>Try another set</button>
            </div>
          </div>
        )}

        {!error && (finished && job
          ? <Completion job={job} failedParts={knownFailed} onRetry={handleRetry}
                        onStartOver={onStartOver} retrying={retrying} />
          : <ProgressPanel job={job} progress={progress} currentAction={action} />)}

        {!error && <div style={{ marginTop: 16 }}><PartsList parts={partList} /></div>}
      </div>
    </main>
  )
}
