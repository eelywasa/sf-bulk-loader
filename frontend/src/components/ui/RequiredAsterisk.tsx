export function RequiredAsterisk() {
  return (
    <>
      <span aria-hidden="true" className="text-error-text">*</span>
      <span className="sr-only"> (required)</span>
    </>
  )
}
