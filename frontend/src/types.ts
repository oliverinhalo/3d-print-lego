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

export type ColorMode = 'none' | 'family' | 'exact'
export type PlateOutput = 'separate' | 'project' | 'both'

export interface EstimateFigures {
  pieces: number
  grams: number
  kilograms: number
  metres: number
  spools: number
  hours: number
  seconds: number
  cost: number
  currency: string
  time_text: string
}

export interface Estimate extends EstimateFigures {
  profile: {
    layer_height_mm: number
    wall_count: number
    infill_percent: number
    density_g_cm3: number
    price_per_kg: number
    currency: string
  }
  plates: Record<string, EstimateFigures>
}

export interface PlateInfo {
  index: number
  group: string
  label: string
  count: number
  fill_percent: number
  bed: [number, number]
  parts: string[]
}

export interface Printer {
  id: string
  label: string
  bed: [number, number]
}

export interface ColorModeOption {
  id: ColorMode
  label: string
  detail: string
}

export interface PlateOutputOption {
  id: PlateOutput
  label: string
  detail: string
}

export interface Options {
  printers: Printer[]
  color_modes: ColorModeOption[]
  plate_outputs: PlateOutputOption[]
  defaults: { bed_preset: string; color_mode: ColorMode; plate_output: PlateOutput }
}

export interface ColorCount {
  color_id: number | null
  color_name: string
  rgb: string
  quantity: number
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
  colors: ColorCount[]
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
  color_mode: ColorMode
  bed_preset: string
  plate_count: number
  plates: PlateInfo[]
  oversized: string[]
  plate_output: PlateOutput
  project_file: string | null
  estimate: Estimate | null
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
  | { type: 'estimate_ready'; estimate: Estimate }
  | { type: 'plates_ready'; plate_count: number; plates: PlateInfo[]; oversized: string[] }
  | { type: 'plate_written'; index: number; name: string; total: number }
  | { type: 'job_complete'; job: JobSummary; failed_parts: Part[] }
  | { type: 'job_failed'; error: string; job?: JobSummary }
  | { type: 'job_cancelled'; job: JobSummary }
  | { type: 'retry_started'; parts: string[] }
  | { type: 'stream_end'; job: JobSummary }
