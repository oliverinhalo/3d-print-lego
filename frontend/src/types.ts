export type PartStatus =
  | 'queued' | 'downloading' | 'converting' | 'validating'
  | 'ready' | 'cached' | 'failed'

export type JobStatus =
  | 'pending' | 'running' | 'complete' | 'partial' | 'failed' | 'cancelled'

export interface LegoSet {
  set_num: string
  display_number: string
  name: string
  year: number | null
  theme: string | null
  num_parts: number
  img_url: string | null
}

export interface Part {
  part_num: string
  name: string
  quantity: number
  status: PartStatus
  geometry_id: string | null
  provider: string | null
  resolution: string | null
  img_url: string | null
  error: string | null
  triangles: number
  dimensions_mm: number[] | null
}

export interface JobSummary {
  job_id: string
  query: string
  status: JobStatus
  stage: string
  stage_label: string
  set: LegoSet | null
  unique_parts: number
  total_pieces: number
  distinct_geometries: number
  ready: number
  failed: number
  files_written: number
  zip_name: string | null
  zip_bytes: number
  error: string | null
  created_at: number
  finished_at: number | null
}

export type JobEvent =
  | { type: 'snapshot'; job: JobSummary }
  | { type: 'job_started'; job: JobSummary }
  | { type: 'stage'; stage: string; label: string; [k: string]: unknown }
  | { type: 'set_found'; set: LegoSet }
  | { type: 'inventory_loaded'; entries: number; total_pieces: number }
  | { type: 'parts_identified'; unique_parts: number; total_pieces: number; parts: Part[] }
  | { type: 'models_resolved'; distinct_geometries: number; matched: number; unmatched: number; parts: Part[] }
  | { type: 'part_progress'; part: Part; completed: number; total: number }
  | { type: 'zip_progress'; written: number; total: number }
  | { type: 'job_complete'; job: JobSummary; failed_parts: Part[] }
  | { type: 'job_failed'; error: string; job?: JobSummary }
  | { type: 'job_cancelled'; job: JobSummary }
  | { type: 'retry_started'; parts: string[] }
  | { type: 'stream_end'; job: JobSummary }
