import { useMemo } from 'react'
import { Radar } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  RadialLinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
} from 'chart.js'

import { getDimensionLabel } from '../utils/dimensionLabels'

ChartJS.register(RadialLinearScale, PointElement, LineElement, Filler, Tooltip)

// Chart.js paints on a <canvas>, which does not resolve CSS custom
// properties the way a normal DOM element's style does — so we read the
// real value out of the CSS variable at render time instead of keeping a
// second hardcoded color here. index.html's :root stays the single
// source of truth for the actual hex values.
function resolveCssColor(variableName, fallback) {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(variableName).trim()
  return value || fallback
}

export default function RadarChart({ dimensions }) {
  const hasInsufficientData = dimensions.some((dim) => dim.score === -1)

  const accentColor = useMemo(() => resolveCssColor('--color-accent', '#3452FF'), [])
  const borderColor = useMemo(() => resolveCssColor('--color-border', '#E4E1D6'), [])

  const data = {
    labels: dimensions.map((dim) => getDimensionLabel(dim.dimension)),
    datasets: [
      {
        label: 'Score',
        // A score of -1 means "insufficient data", plotted as 0 on the
        // chart; the note below the chart makes that distinction clear.
        data: dimensions.map((dim) => (dim.score === -1 ? 0 : dim.score)),
        borderColor: accentColor,
        backgroundColor: `${accentColor}2e`, // ~18% alpha fill
        borderWidth: 2,
        pointBackgroundColor: accentColor,
      },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    // The one entrance animation allowed on this page: the radar expands
    // from the center out to its final shape on first render.
    animation: {
      duration: 1000,
      easing: 'easeOutQuart',
    },
    plugins: {
      legend: { display: false },
    },
    scales: {
      r: {
        min: 0,
        max: 100,
        grid: { color: borderColor },
        angleLines: { color: borderColor },
        ticks: { display: false },
      },
    },
  }

  return (
    <div className="radar-chart">
      <div className="radar-chart-canvas">
        <Radar data={data} options={options} />
      </div>
      {hasInsufficientData && (
        <p className="radar-chart-note">
          * Some dimensions show as 0 due to insufficient data
        </p>
      )}
    </div>
  )
}
