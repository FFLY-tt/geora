// What to show in place of a dimension's score. -1 is never a real score:
// it means the dimension couldn't be measured. When the site itself
// couldn't be read (details.fetch_failure, e.g. "blocked: HTTP 403"),
// say that instead of a generic "not enough data".
export function scoreText(dimension) {
  if (dimension.score !== -1) return String(dimension.score)
  if (dimension.details?.fetch_failure) return "Couldn't read this website"
  return 'Not enough data'
}
