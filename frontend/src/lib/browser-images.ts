// Browser-side image generation: one shared worker, one draw at a time, results
// cached per prompt in the Cache API so a revisit draws nothing.
const IMAGE_CACHE = 'freelingo-images'

let worker: Worker | null = null
let queue: Promise<unknown> = Promise.resolve()

export function browserImagesSupported(): boolean {
  return typeof navigator !== 'undefined' && 'gpu' in navigator
}

function cacheKey(prompt: string): string {
  return `${location.origin}/__image-cache__/${encodeURIComponent(prompt)}`
}

function drawInWorker(baseUrl: string, prompt: string): Promise<Blob> {
  worker ??= new Worker(
    new URL('../workers/image-worker.ts', import.meta.url),
    {
      type: 'module',
    }
  )
  const w = worker
  return new Promise((resolve, reject) => {
    w.onmessage = (event: MessageEvent<{ ok: boolean; blob?: Blob }>) =>
      event.data.ok && event.data.blob
        ? resolve(event.data.blob)
        : reject(new Error('image generation failed'))
    w.onerror = () => reject(new Error('image worker failed'))
    w.postMessage({ baseUrl, prompt })
  })
}

export async function drawImage(
  baseUrl: string,
  prompt: string
): Promise<Blob> {
  const cache = await caches.open(IMAGE_CACHE)
  const hit = await cache.match(cacheKey(prompt))
  if (hit) return hit.blob()
  const run = queue.then(() => drawInWorker(baseUrl, prompt))
  queue = run.catch(() => undefined)
  const blob = await run
  await cache.put(cacheKey(prompt), new Response(blob)).catch(() => undefined)
  return blob
}
