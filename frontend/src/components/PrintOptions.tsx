import type { ColorMode, Options } from '../types'

interface Props {
  options: Options | null
  colorMode: ColorMode
  bedPreset: string
  onColorMode: (mode: ColorMode) => void
  onBedPreset: (preset: string) => void
}

const MODES: ColorMode[] = ['none', 'family', 'exact']

/**
 * The only two choices that change the output, kept behind a disclosure so
 * the default path stays a set number and one button.
 *
 * Colour grouping is a slider rather than a dropdown because the three
 * options are a spectrum — from "ignore colour, fewest plates" to "one
 * plate per exact colour" — and a slider shows that ordering at a glance.
 */
export function PrintOptions({ options, colorMode, bedPreset,
                               onColorMode, onBedPreset }: Props) {
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
