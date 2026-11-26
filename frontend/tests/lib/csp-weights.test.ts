import { describe, expect, it } from 'vitest'

import { buildCsp } from '@/lib/csp'

describe('buildCsp', () => {
  it('limits connect-src to the origin and sockets when no weights URL is set', () => {
    const csp = buildCsp('')
    expect(csp).toContain("connect-src 'self' ws: wss:;")
    expect(csp).not.toContain('https:')
  })

  it('adds https: to connect-src when a weights URL is set', () => {
    const csp = buildCsp('https://huggingface.co/x/resolve/main/')
    expect(csp).toContain("connect-src 'self' ws: wss: https:;")
  })

  it('allows WebAssembly and blob workers in both cases', () => {
    for (const csp of [buildCsp(''), buildCsp('https://w.example/')]) {
      expect(csp).toContain(
        "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'"
      )
      expect(csp).toContain("worker-src 'self' blob:")
      expect(csp).toContain("img-src 'self' data: blob:")
    }
  })
})
