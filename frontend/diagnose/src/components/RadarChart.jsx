import { Radar } from 'react-chartjs-2'
import { Chart as ChartJS, Filler, LineElement, PointElement, RadialLinearScale, Tooltip } from 'chart.js'
import { radarChartData, RADAR_LEGEND, wrapLines } from '../utils/radar.js'

ChartJS.register(RadialLinearScale, PointElement, LineElement, Filler, Tooltip)

// Colors match the page: the gray is the muted ink, the blue is the accent.
const GRAY = '#8A887B'
const BLUE = '#3452FF'
const GRID = '#E4E1D6'
const INK = '#1A1A16'
const MUTED = '#6B6A5E'

// Draws each corner's name, with a small note under it when AI never used that kind of website
// or its pages could not be opened. The chart's own point labels cannot show two sizes of text.
const cornerLabels = {
  id: 'cornerLabels',
  afterDraw(chart, _args, options) {
    const scale = chart.scales.r
    const { ctx } = chart
    ctx.save()
    options.corners.forEach((corner, index) => {
      const point = scale.getPointPosition(index, scale.drawingArea + 16)
      const dx = point.x - scale.xCenter
      const dy = point.y - scale.yCenter
      ctx.textAlign = Math.abs(dx) < 8 ? 'center' : dx > 0 ? 'left' : 'right'
      ctx.textBaseline = 'middle'
      // Lines are stacked away from the chart: above the point at the top, below it elsewhere.
      const top = dy < -8
      const nameY = corner.note ? (top ? point.y - 14 : point.y) : point.y + (top ? -4 : 0)
      const noteY = top ? point.y : point.y + 14
      ctx.fillStyle = INK
      ctx.font = '600 13px "IBM Plex Sans", sans-serif'
      ctx.fillText(corner.label, point.x, nameY)
      if (corner.note) {
        ctx.fillStyle = MUTED
        ctx.font = '400 11px "IBM Plex Sans", sans-serif'
        ctx.fillText(corner.note, point.x, noteY)
      }
    })
    ctx.restore()
  },
}

// Props: radar (result.radar). Two shapes on one scale: gray is where AI looks for answers,
// blue is the part of it that mentions the company. Pointing at a corner shows one sentence.
export default function RadarChart({ radar }) {
  if (!radar || !Array.isArray(radar.axes) || radar.axes.length === 0) return null
  const { corners, labels, gray, blue, scaleMax } = radarChartData(radar)
  const stepSize = scaleMax % 20 === 0 ? scaleMax / 4 : scaleMax / 5

  const data = {
    labels,
    datasets: [
      {
        label: RADAR_LEGEND.gray,
        data: gray,
        backgroundColor: 'rgba(138, 136, 123, 0.22)',
        borderColor: GRAY,
        borderWidth: 2,
        pointBackgroundColor: GRAY,
        pointRadius: 3,
        pointHoverRadius: 5,
      },
      {
        label: RADAR_LEGEND.blue,
        data: blue,
        backgroundColor: 'rgba(52, 82, 255, 0.32)',
        borderColor: BLUE,
        borderWidth: 2,
        pointBackgroundColor: BLUE,
        pointRadius: 3,
        pointHoverRadius: 5,
      },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: true,
    aspectRatio: 1.2,
    layout: { padding: { top: 34, bottom: 34, left: 130, right: 130 } },
    interaction: { mode: 'index', intersect: false },
    scales: {
      r: {
        min: 0,
        max: scaleMax,
        ticks: { stepSize, backdropColor: 'transparent', color: MUTED, font: { size: 10 } },
        grid: { color: GRID },
        angleLines: { color: GRID },
        pointLabels: { display: false },
      },
    },
    plugins: {
      legend: { display: false },
      cornerLabels: { corners },
      tooltip: {
        // One sentence per corner, shown once (on the gray point), whichever shape is pointed at.
        filter: (item) => item.datasetIndex === 0,
        displayColors: false,
        callbacks: {
          title: () => '',
          label: (context) => wrapLines(corners[context.dataIndex].sentence),
        },
      },
    },
  }

  return (
    <figure className="radar">
      <div className="radar-canvas">
        <Radar data={data} options={options} plugins={[cornerLabels]} aria-label="Where AI looks for answers and where it finds you" role="img" />
      </div>
      <figcaption className="radar-legend">
        <span className="radar-legend-item">
          <span className="radar-swatch radar-swatch-gray" />
          {RADAR_LEGEND.gray}
        </span>
        <span className="radar-legend-item">
          <span className="radar-swatch radar-swatch-blue" />
          {RADAR_LEGEND.blue}
        </span>
      </figcaption>
      <p className="radar-scale">The numbers are out of every 100 sources AI used.</p>
      <ul className="sr-only">
        {corners.map((corner) => (
          <li key={corner.key}>
            {corner.label}. {corner.sentence}
          </li>
        ))}
      </ul>
    </figure>
  )
}
