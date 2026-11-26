// Draws one image per message. The runtime comes from this origin (public/ort-image,
// copied by scripts/copy-ort-image.js); the weights come from the base URL in the message.
import { JeverationPipeline } from '@/lib/image/jeveration'

const WEIGHTS_CACHE = 'freelingo-image-weights'
const ORT_URL = '/ort-image/'

let loaded: { baseUrl: string; pipeline: Promise<JeverationPipeline> } | null =
  null

async function cached(url: string): Promise<Response> {
  const cache = await caches.open(WEIGHTS_CACHE)
  const hit = await cache.match(url)
  if (hit) return hit
  const res = await fetch(url)
  if (!res.ok) throw new Error(`weights fetch failed: ${res.status}`)
  await cache.put(url, res)
  return (await cache.match(url)) as Response
}

async function loadPipeline(baseUrl: string): Promise<JeverationPipeline> {
  const base = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
  const ort = await import(
    /* webpackIgnore: true */ `${ORT_URL}ort.webgpu.min.mjs`
  )
  ort.env.wasm.wasmPaths = ORT_URL
  ort.env.wasm.numThreads = 1
  const files = {
    bytes: async (path: string) =>
      new Uint8Array(await (await cached(base + path)).arrayBuffer()),
    json: async (path: string) => (await cached(base + path)).json(),
  }
  return JeverationPipeline.load(files, {
    ort,
    device: 'webgpu',
  } as { device?: string })
}

async function draw(baseUrl: string, prompt: string): Promise<Blob> {
  if (loaded?.baseUrl !== baseUrl) {
    loaded = { baseUrl, pipeline: loadPipeline(baseUrl) }
  }
  const pipeline = await loaded.pipeline.catch((error) => {
    loaded = null
    throw error
  })
  const rgb: Uint8Array = await pipeline.generate(prompt)
  const rgba = new Uint8ClampedArray(512 * 512 * 4)
  for (let i = 0; i < 512 * 512; i++) {
    rgba[i * 4] = rgb[i * 3]
    rgba[i * 4 + 1] = rgb[i * 3 + 1]
    rgba[i * 4 + 2] = rgb[i * 3 + 2]
    rgba[i * 4 + 3] = 255
  }
  const canvas = new OffscreenCanvas(512, 512)
  canvas.getContext('2d')!.putImageData(new ImageData(rgba, 512, 512), 0, 0)
  return canvas.convertToBlob({ type: 'image/png' })
}

self.onmessage = async (
  event: MessageEvent<{ baseUrl: string; prompt: string }>
) => {
  try {
    const blob = await draw(event.data.baseUrl, event.data.prompt)
    self.postMessage({ ok: true, blob })
  } catch {
    self.postMessage({ ok: false })
  }
}
