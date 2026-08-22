import type { JobEvent, JobSummary, LegoSet } from '../types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      if (body?.detail) detail = typeof body.detail === 'string'
        ? body.detail
        : (body.detail?.[0]?.msg ?? detail)
    } catch { /* keep the generic message */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export function startGeneration(setNumber: string) {
  return request<{ job_id: string; set_num: string }>('/generate', {
    method: 'POST',
    body: JSON.stringify({ set_number: setNumber }),
  })
}

export function previewSet(setNumber: string) {
  return request<{ set: LegoSet }>(`/sets/${encodeURIComponent(setNumber)}`)
}

export function searchSets(query: string) {
  return request<{ results: LegoSet[] }>(`/sets?q=${encodeURIComponent(query)}`)
}

export function getJob(jobId: string) {
  return request<JobSummary>(`/jobs/${encodeURIComponent(jobId)}`)
}

export function retryFailed(jobId: string) {
  return request<{ job_id: string; retrying: number }>(
    `/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' })
}

export function downloadUrl(jobId: string) {
  return `${BASE}/jobs/${encodeURIComponent(jobId)}/download`
}

/**
 * Subscribe to a job's progress stream.
 *
 * Uses Server-Sent Events, and falls back to polling the job summary if the
 * stream cannot be opened (a proxy that buffers, for example).
 */
export function subscribe(
  jobId: string,
  onEvent: (event: JobEvent) => void,
  onError?: (message: string) => void,
): () => void {
  let closed = false
  let pollTimer: number | undefined

  const source = new EventSource(`${BASE}/jobs/${encodeURIComponent(jobId)}/events`)

  source.onmessage = (message) => {
    if (closed) return
    try {
      onEvent(JSON.parse(message.data) as JobEvent)
    } catch { /* ignore malformed frame */ }
  }

  source.onerror = () => {
    if (closed) return
    source.close()
    // Fall back to polling so progress still advances.
    const poll = async () => {
      if (closed) return
      try {
        const job = await getJob(jobId)
        onEvent({ type: 'snapshot', job })
        if (job.status === 'complete' || job.status === 'partial') {
          onEvent({ type: 'job_complete', job, failed_parts: [] })
          return
        }
        if (job.status === 'failed') {
          onEvent({ type: 'job_failed', error: job.error ?? 'Generation failed.', job })
          return
        }
      } catch (error) {
        onError?.((error as Error).message)
        return
      }
      pollTimer = window.setTimeout(poll, 1000)
    }
    pollTimer = window.setTimeout(poll, 500)
  }

  return () => {
    closed = true
    source.close()
    if (pollTimer) window.clearTimeout(pollTimer)
  }
}
