/** Production Content-Security-Policy. `weightsUrl` is IMAGE_MODEL_BASE_URL: when set,
 *  the browser may fetch the image weights over https, so connect-src adds `https:`. */
export function buildCsp(weightsUrl: string): string {
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self' ws: wss:${weightsUrl ? ' https:' : ''}`,
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
  ].join('; ')
}
