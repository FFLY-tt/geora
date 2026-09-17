// Backend results are reused as-is for 24 hours per domain — there is no
// "check again" action anywhere in this app. This one-line notice exists
// so a user who tests the same domain twice in a row understands why the
// numbers are identical, without needing a timestamp or explanation.
export default function CacheNotice() {
  return <p className="cache-notice">This result will stay the same for the next 24 hours.</p>
}
