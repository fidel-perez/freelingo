'use client'

import { useTranslations } from 'next-intl'
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { browserImagesSupported, drawImage } from '@/lib/browser-images'
import { useAuthStore } from '@/store/auth'

type ImageState =
  'loading' | 'done' | 'failed' | 'unsupported' | 'server-loading' | 'server'

const POLL_MS = 3000
const POLL_LIMIT_MS = 4 * 60 * 1000
const BACKOFF_MS = 10_000
const DRAW_LIMIT_MS = 20_000

/** Draws the scene in the browser, or fetches the Mac-drawn image when the browser cannot; `data-image-state` follows it. */
export function PictureTalkImage({
  prompt,
  sceneId,
  words = [],
}: {
  prompt: string
  sceneId: number
  words?: string[]
}) {
  const enabled = useAuthStore((s) => s.user?.images_enabled)
  const baseUrl = useAuthStore((s) => s.user?.image_model_base_url)
  const t = useTranslations('pictureTalk')
  const canDraw = !!enabled && !!baseUrl && browserImagesSupported()
  const [drawFailed, setDrawFailed] = useState(false)
  const [retryKey, setRetryKey] = useState(0)
  const useServer = !!enabled && (!canDraw || drawFailed)
  const [drawState, setState] = useState<ImageState>('loading')
  const [serverState, setServerState] = useState<ImageState>('server-loading')
  const [src, setSrc] = useState('')
  const state: ImageState = useServer
    ? serverState
    : canDraw
      ? drawState
      : 'unsupported'

  useEffect(() => {
    if (!canDraw || !baseUrl) return
    let cancelled = false
    let url = ''
    setState('loading')
    setSrc('')
    setDrawFailed(false)
    // Any draw failure, or no image after DRAW_LIMIT_MS, hands the scene to the image server.
    const giveUp = () => {
      if (!cancelled) setDrawFailed(true)
    }
    const limit = setTimeout(giveUp, DRAW_LIMIT_MS)
    void drawImage(baseUrl, prompt)
      .then((blob) => {
        clearTimeout(limit)
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setSrc(url)
        setState('done')
      })
      .catch(() => {
        clearTimeout(limit)
        giveUp()
      })
    return () => {
      cancelled = true
      clearTimeout(limit)
      if (url) URL.revokeObjectURL(url)
    }
  }, [canDraw, baseUrl, prompt])

  useEffect(() => {
    if (!useServer) return
    let cancelled = false
    let url = ''
    let timer: ReturnType<typeof setTimeout> | undefined
    const deadline = Date.now() + POLL_LIMIT_MS
    setServerState('server-loading')
    setSrc('')
    // The host answers 202 while it draws; ask again every POLL_MS until the image or the deadline.
    let retried503 = false
    const later = (ms: number) => {
      if (Date.now() >= deadline) throw new Error('image timed out')
      timer = setTimeout(attempt, ms)
    }
    const attempt = () => {
      apiFetch('/api/picture-talk/image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene_id: sceneId }),
      })
        .then(async (res) => {
          if (cancelled) return
          if (res.status === 202) return later(POLL_MS)
          // 429 means the rate limit: back off and keep polling. 503 gets one retry.
          if (res.status === 429) return later(BACKOFF_MS)
          if (res.status === 503 && !retried503) {
            retried503 = true
            return later(POLL_MS)
          }
          if (!res.ok) throw new Error('image server failed')
          const blob = await res.blob()
          if (cancelled) return
          url = URL.createObjectURL(blob)
          setSrc(url)
          setServerState('server')
        })
        .catch(() => {
          if (!cancelled) setServerState('failed')
        })
    }
    attempt()
    return () => {
      cancelled = true
      clearTimeout(timer)
      if (url) URL.revokeObjectURL(url)
    }
  }, [useServer, sceneId, retryKey])

  const showWords =
    state === 'unsupported' || state === 'failed' || state === 'server-loading'

  return (
    <div
      data-picture-talk-image
      data-image-state={state}
      className="border-fl-border bg-fl-surface relative mx-auto mb-4 aspect-square w-full max-w-full shrink-0 overflow-hidden border sm:max-w-xs"
    >
      {(state === 'done' || state === 'server') && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className="h-full w-full object-cover" />
      )}
      {state === 'failed' && (
        <button
          type="button"
          onClick={() => setRetryKey((n) => n + 1)}
          className="border-fl-border text-fl-text absolute bottom-2 left-1/2 -translate-x-1/2 border px-3 py-1 font-sans text-sm"
        >
          {t('imageRetry')}
        </button>
      )}
      {showWords && (
        <p
          className={`text-fl-muted-1 flex h-full flex-wrap content-center items-center justify-center gap-2 p-4 text-center font-sans text-lg ${state === 'server-loading' ? 'animate-pulse' : ''}`}
        >
          {words.map((word, index) => (
            <span key={index}>{word}</span>
          ))}
        </p>
      )}
    </div>
  )
}
