import { useCallback } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { runsApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import { useToast } from '../components/ui/Toast'

interface UseRunActionsOptions {
  runId: string
  /** Called when abort completes (success or error) — use to close a confirmation modal */
  onAbortSettled?: () => void
}

export function useRunActions({ runId, onAbortSettled }: UseRunActionsOptions) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { success: toastSuccess, error: toastError } = useToast()

  const retryMutation = useMutation({
    mutationFn: ({ stepId }: { stepId: string }) => runsApi.retryStep(runId, stepId),
    onSuccess: (newRun) => {
      navigate(`/runs/${newRun.id}`)
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) {
        toastError(`Retry failed: ${err.message}`)
      } else {
        toastError('Failed to start retry run.')
      }
    },
  })

  const abortMutation = useMutation({
    mutationFn: () => runsApi.abort(runId),
    onSuccess: () => {
      toastSuccess('Abort request sent.')
      onAbortSettled?.()
      void queryClient.invalidateQueries({ queryKey: ['runs', runId] })
    },
    onError: (err: unknown) => {
      onAbortSettled?.()
      if (err instanceof ApiError && err.status === 409) {
        toastError('Run is not abortable (already finished or abort in progress).')
      } else {
        toastError('Failed to abort run.')
      }
    },
  })

  const retryStep = useCallback(
    (stepId: string) => retryMutation.mutate({ stepId }),
    [retryMutation],
  )

  const abort = useCallback(() => abortMutation.mutate(), [abortMutation])

  return {
    retryStep,
    isRetryPending: retryMutation.isPending,
    retryVariables: retryMutation.variables,
    abort,
    isAbortPending: abortMutation.isPending,
  }
}
