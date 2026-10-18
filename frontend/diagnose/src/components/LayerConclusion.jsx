// The one-paragraph conclusion under the headline: which kind of website
// AI reads that doesn't mention you. The sentences come from the backend's
// fixed templates (source_layers.conclusion).
export default function LayerConclusion({ sourceLayers }) {
  const sentences = sourceLayers?.conclusion
  if (!Array.isArray(sentences) || sentences.length === 0) return null

  return (
    <div className="layer-conclusion">
      {sentences.map((sentence) => (
        <p key={sentence}>{sentence}</p>
      ))}
    </div>
  )
}
