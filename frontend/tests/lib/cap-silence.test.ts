import { describe, expect, it } from 'vitest'
import { capSilence } from '@/lib/cap-silence'
import { float32ToWav } from '@/lib/audio'

const RATE = 22050

function tone(seconds: number): number[] {
  return Array.from(
    { length: Math.round(seconds * RATE) },
    (_, i) => 0.5 * Math.sin((2 * Math.PI * 440 * i) / RATE)
  )
}

function wav(parts: number[][]): ArrayBuffer {
  return float32ToWav(Float32Array.from(parts.flat()), RATE)
}

function silence(seconds: number): number[] {
  return new Array(Math.round(seconds * RATE)).fill(0)
}

describe('capSilence', () => {
  it('shortens a long inner pause and fixes the header sizes', () => {
    const input = wav([tone(0.3), silence(1), tone(0.3)])
    const out = capSilence(input)
    expect(out.byteLength).toBeLessThan(input.byteLength / 2)
    const view = new DataView(out)
    expect(view.getUint32(4, true)).toBe(out.byteLength - 8)
    expect(view.getUint32(40, true)).toBe(out.byteLength - 44)
  })

  it('shortens a long lead and trail but never to zero samples', () => {
    const input = wav([silence(1), tone(0.3), silence(1)])
    const out = capSilence(input)
    expect(out.byteLength).toBeGreaterThan(44 + 0.3 * RATE * 2)
    expect(out.byteLength).toBeLessThan(44 + 0.4 * RATE * 2)
  })

  it('returns the same buffer when nothing is long', () => {
    const input = wav([tone(0.3), silence(0.01), tone(0.3)])
    expect(capSilence(input)).toBe(input)
  })

  it('passes non-WAV bytes through unchanged', () => {
    const bytes = new Uint8Array(200).fill(7).buffer
    expect(capSilence(bytes)).toBe(bytes)
  })

  it('passes a WAV that is not 16-bit mono through unchanged', () => {
    const input = wav([tone(0.3), silence(1), tone(0.3)])
    new DataView(input).setUint16(22, 2, true)
    expect(capSilence(input)).toBe(input)
  })
})
