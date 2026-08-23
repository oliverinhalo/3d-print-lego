import { useEffect, useRef, useState } from 'react'
import { PrintOptions } from '../components/PrintOptions'
import { getOptions, searchSets, startGeneration } from '../lib/api'
import type { ColorMode, LegoSet, Options, PlateOutput } from '../types'

const STEPS = [
  { num: '01', label: 'Enter your set', detail: 'Any LEGO set number' },
  { num: '02', label: 'We find every part', detail: 'Full inventory, deduplicated' },
  { num: '03', label: 'Models prepared', detail: 'Validated, millimetre-accurate' },
  { num: '04', label: 'Packed onto plates', detail: 'Grouped by colour' },
  { num: '05', label: 'Open a plate', detail: 'Already arranged' },
  { num: '06', label: 'Print', detail: 'No arranging needed' },
]

interface HomeProps {
  onStarted: (jobId: string) => void
  onBrowse: () => void
  preset?: string
}

export function Home({ onStarted, onBrowse, preset }: HomeProps) {
  const [value, setValue] = useState(preset ?? '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [suggestions, setSuggestions] = useState<LegoSet[]>([])
  const [options, setOptions] = useState<Options | null>(null)
  const [colorMode, setColorMode] = useState<ColorMode>('family')
  const [bedPreset, setBedPreset] = useState('bambu_p1')
  const [plateOutput, setPlateOutput] = useState<PlateOutput>('separate')
  const [maxColors, setMaxColors] = useState(4)
  const [showOptions, setShowOptions] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // A set chosen on the browse screen lands here ready to generate.
  useEffect(() => { if (preset) setValue(preset) }, [preset])

  // Defaults come from the server so the UI never hard-codes printer lists.
  useEffect(() => {
    getOptions()
      .then(loaded => {
        setOptions(loaded)
        setColorMode(loaded.defaults.color_mode)
        setBedPreset(loaded.defaults.bed_preset)
        setPlateOutput(loaded.defaults.plate_output)
        setMaxColors(loaded.defaults.max_colors)
      })
      .catch(() => { /* the defaults above are fine on their own */ })
  }, [])

  async function submit(setNumber: string) {
    const query = setNumber.trim()
    if (!query) {
      setError('Enter a LEGO set number to get started.')
      inputRef.current?.focus()
      return
    }
    setBusy(true)
    setError('')
    setSuggestions([])
    try {
      const { job_id } = await startGeneration(query, {
        color_mode: colorMode,
        bed_preset: bedPreset,
        plate_output: plateOutput,
        max_colors: maxColors,
      })
      onStarted(job_id)
    } catch (err) {
      const message = (err as Error).message
      setError(message)
      // A number we cannot find is usually a typo — offer close matches.
      if (/not found|catalogue/i.test(message)) {
        try {
          const { results } = await searchSets(query.replace(/[^0-9a-z ]/gi, ''))
          setSuggestions(results.slice(0, 4))
        } catch { /* suggestions are a bonus, never a blocker */ }
      }
      setBusy(false)
    }
  }

  return (
    <main className="home">
      <section className="hero">
        <h1>Build your LEGO set.<span>Print every piece.</span></h1>
        <p className="lede">
          Enter a set number and we'll prepare the complete set of printable
          3D models — ready to drop straight into your slicer.
        </p>

        <form className="search"
              onSubmit={(e) => { e.preventDefault(); void submit(value) }}>
          <button type="button" className="browse-link" onClick={onBrowse}>
            Browse all LEGO sets →
          </button>

          <div className="search-field">
            <span className="hash">#</span>
            <input
              ref={inputRef}
              value={value}
              onChange={(e) => { setValue(e.target.value); setError('') }}
              placeholder="77263"
              aria-label="LEGO set number"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
          </div>

          {error && <div className="form-error">{error}</div>}

          <button className="btn-primary" type="submit" disabled={busy}>
            {busy ? 'Starting…' : 'Generate Printable Set'}
          </button>

          <button type="button" className="disclosure"
                  onClick={() => setShowOptions(v => !v)}
                  aria-expanded={showOptions}>
            {showOptions ? 'Hide options' : 'Options'}
          </button>

          {showOptions && (
            <PrintOptions
              options={options}
              colorMode={colorMode}
              bedPreset={bedPreset}
              plateOutput={plateOutput}
              maxColors={maxColors}
              onColorMode={setColorMode}
              onMaxColors={setMaxColors}
              onBedPreset={setBedPreset}
              onPlateOutput={setPlateOutput}
            />
          )}
        </form>

        {suggestions.length > 0 && (
          <div className="suggestions">
            <h4>Did you mean</h4>
            {suggestions.map(set => (
              <button className="suggestion" key={set.set_num}
                      onClick={() => { setValue(set.display_number); void submit(set.set_num) }}>
                {set.img_url
                  ? <img src={set.img_url} alt="" loading="lazy" />
                  : <span style={{ width: 46 }} />}
                <span className="meta">
                  <strong>{set.name}</strong>
                  <span>#{set.display_number} · {set.num_parts} pieces</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="how">
        <h3>How it works</h3>
        <div className="steps">
          {STEPS.map(step => (
            <div className="step" key={step.num}>
              <div className="num">{step.num}</div>
              <div className="label">{step.label}</div>
              <div className="detail">{step.detail}</div>
            </div>
          ))}
        </div>
      </section>

      <p className="footnote">
        Geometry comes from the open <a href="https://library.ldraw.org/"
        target="_blank" rel="noreferrer">LDraw Parts Library</a> (CC BY); inventories
        come from the public <a href="https://rebrickable.com/downloads/"
        target="_blank" rel="noreferrer">Rebrickable</a> dataset. For personal use.
        LEGO® is a trademark of the LEGO Group, which does not sponsor or endorse
        this project.
      </p>
    </main>
  )
}
