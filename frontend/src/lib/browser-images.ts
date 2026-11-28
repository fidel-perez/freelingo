// Browser-side image generation: one shared worker, one draw at a time, results
// cached per prompt in the Cache API so a revisit draws nothing.
const IMAGE_CACHE = 'freelingo-images'

let worker: Worker | null = null
const pending = new Set<(error: Error) => void>()
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
    pending.add(reject)
    w.onmessage = (event: MessageEvent<{ ok: boolean; blob?: Blob }>) => {
      pending.delete(reject)
      if (event.data.ok && event.data.blob) resolve(event.data.blob)
      else reject(new Error('image generation failed'))
    }
    w.onerror = () => {
      const error = new Error('image worker failed')
      pending.forEach((fail) => fail(error))
      pending.clear()
      w.terminate()
      if (worker === w) worker = null
    }
    w.postMessage({ baseUrl, prompt })
  })
}

// Rejects before any download when the GPU cannot run the model: no adapter, or no shader-f16.
async function requireGpu(): Promise<void> {
  const gpu = (
    navigator as Navigator & {
      gpu?: {
        requestAdapter: () => Promise<{ features: Set<string> } | null>
      }
    }
  ).gpu
  const adapter = await gpu?.requestAdapter()
  if (!adapter) throw new Error('no WebGPU adapter')
  if (!adapter.features.has('shader-f16')) throw new Error('no shader-f16')
}

export async function drawImage(
  baseUrl: string,
  prompt: string
): Promise<Blob> {
  const cache = await caches.open(IMAGE_CACHE)
  const hit = await cache.match(cacheKey(prompt))
  if (hit) return hit.blob()
  await requireGpu()
  const run = queue.then(() => drawInWorker(baseUrl, prompt))
  queue = run.catch(() => undefined)
  const blob = await run
  await cache.put(cacheKey(prompt), new Response(blob)).catch(() => undefined)
  return blob
}
