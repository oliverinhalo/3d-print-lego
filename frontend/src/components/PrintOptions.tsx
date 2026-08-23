import type { ColorMode, Options, PlateOutput } from '../types'

interface Props {
  options: Options | null
  colorMode: ColorMode
  bedPreset: string
  plateOutput: PlateOutput
  maxColors: number
  onColorMode: (mode: ColorMode) => void
  onMaxColors: (value: number) => void
  onBedPreset: (preset: string) => void
  onPlateOutput: (output: PlateOutput) => void
}

const MODES: ColorMode[] = ['none', 'family', 'exact']

/**
 * The handful of choices that change the output, kept behind a disclosure so
 * the default path stays a set number and one button.
 *
 * Colour grouping is a slider rather than a dropdown because the three
 * options are a spectrum — from "ignore colour, fewest plates" to "one
 * plate per exact colour" — and a slider shows that ordering at a glance.
 */
export function PrintOptions({ options, colorMode, bedPreset, plateOutput, maxColors,
                               onColorMode, onBedPreset, onPlateOutput,
                               onMaxColors }: Props) {
  const modeIndex = Math.max(0, MODES.indexOf(colorMode))
  const current = options?.color_modes.find(m => m.id === colorMode)

  return (
    <div className="options">
      <div className="option-block">
        <div className="option-head">
          <label htmlFor="colour-mode">Group plates by colour</label>
          <span className="option-value">{current?.label ?? 'Similar colours'}</span>
        </div>

        <input
          id="colour-mode"
          className="slider"
          type="range"
          min={0}
          max={MODES.length - 1}
          step={1}
          value={modeIndex}
          onChange={(e) => onColorMode(MODES[Number(e.target.value)])}
          aria-describedby="colour-mode-detail"
        />

        <div className="slider-ticks">
          {options?.color_modes.map(mode => (
            <button
              type="button"
              key={mode.id}
              className={`tick${mode.id === colorMode ? ' on' : ''}`}
              onClick={() => onColorMode(mode.id)}
            >
              {mode.label}
            </button>
          )) ?? <span />}
        </div>

        <p className="option-detail" id="colour-mode-detail">
          {current?.detail ?? 'All reds together, all blues together'}
        </p>
      </div>

      {colorMode !== 'none' && (
        <div className="option-block">
          <div className="option-head">
            <label htmlFor="max-colours">Filament colours you own</label>
            <span className="option-value">
              {maxColors === 0 ? 'No limit' : maxColors}
            </span>
          </div>
          <div className="limit-group">
            {(options?.color_limits ?? [1, 2, 3, 4, 5, 6, 8, 12, 0]).map(limit => (
              <button
                type="button"
                key={limit}
                className={`limit${limit === maxColors ? ' on' : ''}`}
                onClick={() => onMaxColors(limit)}
              >
                {limit === 0 ? 'All' : limit}
              </button>
            ))}
          </div>
          <p className="option-detail">
            The closest colours are merged until the set needs no more than
            this many. Fewer means fewer spools — and fewer plates.
          </p>
        </div>
      )}

      <div className="option-block">
        <div className="option-head">
          <label>Files</label>
        </div>
        <div className="choice-group">
          {options?.plate_outputs.map(choice => (
            <button
              type="button"
              key={choice.id}
              className={`choice${choice.id === plateOutput ? ' on' : ''}`}
              onClick={() => onPlateOutput(choice.id)}
            >
              <strong>{choice.label}</strong>
              <span>{choice.detail}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="option-block">
        <div className="option-head">
          <label htmlFor="printer">Printer</label>
        </div>
        <select
          id="printer"
          className="select"
          value={bedPreset}
          onChange={(e) => onBedPreset(e.target.value)}
        >
          {options?.printers.map(printer => (
            <option key={printer.id} value={printer.id}>
              {printer.label} — {printer.bed[0]} × {printer.bed[1]} mm
            </option>
          ))}
        </select>
        <p className="option-detail">
          Parts are arranged to fit this bed, so plates open ready to print.
        </p>
      </div>
    </div>
  )
}
