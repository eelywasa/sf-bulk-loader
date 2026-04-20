import { useState } from 'react'
import { stepsApi } from '../api/endpoints'
import type { LoadStep } from '../api/types'
import { isQueryOp, type PreviewEntry } from '../pages/planEditorUtils'

export function useStepPreview(planId: string | undefined) {
  const [previews, setPreviews] = useState<Record<string, PreviewEntry>>({})
  const [preflightOpen, setPreflightOpen] = useState(false)

  async function handlePreviewStep(step: LoadStep) {
    // Query ops use inline SOQL validation — the Preview button is hidden for them.
    // Early-return here as a safety guard so no request is ever fired.
    if (isQueryOp(step.operation)) return
    if (!planId) return
    setPreviews((prev) => ({ ...prev, [step.id]: { status: 'loading' } }))
    try {
      const data = await stepsApi.preview(planId, step.id)
      setPreviews((prev) => ({ ...prev, [step.id]: { status: 'success', data } }))
    } catch (err) {
      setPreviews((prev) => ({
        ...prev,
        [step.id]: {
          status: 'error',
          message: err instanceof Error ? err.message : 'Preview failed',
        },
      }))
    }
  }

  async function handlePreflight(steps: LoadStep[]) {
    if (!planId) return
    setPreflightOpen(true)
    // Exclude query ops from preflight — they use server-side SOQL validation instead
    const dmlSteps = steps.filter((s) => !isQueryOp(s.operation))
    setPreviews((prev) => {
      const next = { ...prev }
      for (const step of dmlSteps) {
        next[step.id] = { status: 'loading' }
      }
      return next
    })
    await Promise.all(
      dmlSteps.map(async (step) => {
        try {
          const data = await stepsApi.preview(planId, step.id)
          setPreviews((prev) => ({ ...prev, [step.id]: { status: 'success', data } }))
        } catch (err) {
          setPreviews((prev) => ({
            ...prev,
            [step.id]: {
              status: 'error',
              message: err instanceof Error ? err.message : 'Preview failed',
            },
          }))
        }
      }),
    )
  }

  return { previews, preflightOpen, setPreflightOpen, handlePreviewStep, handlePreflight }
}
