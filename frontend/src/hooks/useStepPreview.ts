import { useState } from 'react'
import { stepsApi } from '../api/endpoints'
import type { LoadStep } from '../api/types'
import type { PreviewEntry } from '../pages/planEditorUtils'

export function useStepPreview(planId: string | undefined) {
  const [previews, setPreviews] = useState<Record<string, PreviewEntry>>({})
  const [preflightOpen, setPreflightOpen] = useState(false)

  async function handlePreviewStep(step: LoadStep) {
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
    setPreviews((prev) => {
      const next = { ...prev }
      for (const step of steps) {
        next[step.id] = { status: 'loading' }
      }
      return next
    })
    await Promise.all(
      steps.map(async (step) => {
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
