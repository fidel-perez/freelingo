'use client'

import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { browserImagesSupported, drawImage } from '@/lib/browser-images'
import { useAuthStore } from '@/store/auth'

/** Draws a lesson or situation image in the browser. Shows nothing when
 *  images are off, WebGPU is missing, or anything fails. */
export function BrowserImage({ promptPath }: { promptPath: string }) {
  const enabled = useAuthStore((s) => s.user?.images_enabled)
  const baseUrl = useAuthStore((s) => s.user?.image_model_base_url)
  const [state, setState] = useState<'loading' | 'done' | 'failed'>('loading')
  const [src, setSrc] = useState('')

  useEffect(() => {
    if (!enabled || !baseUrl || !browserImagesSupported()) return
    let cancelled = false
    let url = ''
    setState('loading')
    void (async () => {
      try {
        const res = await apiFetch(promptPath)
        if (!res.ok) throw new Error('prompt')
        const { prompt } = await res.json()
        const blob = await drawImage(baseUrl, prompt)
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setSrc(url)
        setState('done')
      } catch {
        if (!cancelled) setState('failed')
      }
    })()
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [enabled, baseUrl, promptPath])

  if (!enabled || !baseUrl || !browserImagesSupported() || state === 'failed') {
    return null
  }

  return (
    <div
      data-image-state={state}
      className="border-fl-border bg-fl-surface mx-auto aspect-square w-full max-w-sm overflow-hidden border"
    >
      {state === 'done' && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className="h-full w-full object-cover" />
      )}
    </div>
  )
}
