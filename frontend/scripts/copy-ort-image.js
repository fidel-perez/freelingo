// Copies the onnxruntime-web WebGPU runtime into public/ort-image/ so the image
// worker loads it from the frontend's own origin (script-src 'self' blocks CDNs).
const fs = require('fs')
const path = require('path')

const src = path.join(__dirname, '../node_modules/onnxruntime-web/dist')
const dst = path.join(__dirname, '../public/ort-image')
const files = [
  'ort.webgpu.min.mjs',
  'ort-wasm-simd-threaded.asyncify.mjs',
  'ort-wasm-simd-threaded.asyncify.wasm',
]

if (!fs.existsSync(src)) {
  console.warn('[copy-ort-image] onnxruntime-web/dist not found, skipping.')
} else {
  fs.mkdirSync(dst, { recursive: true })
  for (const f of files) {
    fs.copyFileSync(path.join(src, f), path.join(dst, f))
    console.log(`[copy-ort-image] copied ${f}`)
  }
}
