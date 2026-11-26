// Shortens silent runs in 16-bit mono WAV; other bytes pass through unchanged.

const HEADER = 44
// -36 dBFS: a fade-out above -50 dBFS is heard as a pause.
export const EVEN_SILENCE = Math.round(0.016 * 32768)
export const EVEN_RMS_S = 0.005
export const SEAM_S = 0.04
// A zero-sample WAV has no duration, so the lead is never zero.
export const MAX_LEAD_S = 0.01
export const MAX_TRAIL_S = SEAM_S - MAX_LEAD_S
export const EVEN_GAP_S = 0.02

export interface CapSilenceOptions {
  maxLeadS?: number
  maxTrailS?: number
  gapS?: number
  silence?: number
}

interface Run {
  start: number
  len: number
  cap: number
}

function rmsBelow(
  samples: Int16Array,
  gate: number,
  win: number
): (i: number) => boolean {
  const sq = new Float64Array(samples.length + 1)
  for (let i = 0; i < samples.length; i++)
    sq[i + 1] = sq[i] + samples[i] * samples[i]
  const half = win >> 1
  return (i) => {
    const a = Math.max(0, i - half)
    const b = Math.min(samples.length, i + half + 1)
    return sq[b] - sq[a] < gate * gate * (b - a)
  }
}

export function capSilence(
  buffer: ArrayBuffer,
  {
    maxLeadS = MAX_LEAD_S,
    maxTrailS = MAX_TRAIL_S,
    gapS = EVEN_GAP_S,
    silence = EVEN_SILENCE,
  }: CapSilenceOptions = {}
): ArrayBuffer {
  if (!buffer || buffer.byteLength <= HEADER) return buffer
  const view = new DataView(buffer)
  const isWav =
    view.getUint32(0) === 0x52494646 && view.getUint32(8) === 0x57415645
  if (!isWav) return buffer
  const channels = view.getUint16(22, true)
  const bits = view.getUint16(34, true)
  const rate = view.getUint32(24, true)
  if (channels !== 1 || bits !== 16 || !rate) return buffer

  const samples = new Int16Array(
    buffer,
    HEADER,
    (buffer.byteLength - HEADER) >> 1
  )
  const maxPause = Math.round(gapS * rate)
  const maxLead = Math.round(maxLeadS * rate)
  const maxTrail = Math.round(maxTrailS * rate)

  // Only runs past their cap are recorded; shorter runs are word noise.
  const runs: Run[] = []
  let runStart = -1
  let streak = 0
  let deep = 0
  const quietAround = rmsBelow(samples, silence, Math.round(EVEN_RMS_S * rate))
  for (let i = 0; i <= samples.length; i++) {
    const still = i < samples.length && Math.abs(samples[i]) < silence
    const quiet = still || (i < samples.length && quietAround(i))
    streak = still ? streak + 1 : 0
    deep = Math.max(deep, streak)
    if (quiet) {
      if (runStart < 0) runStart = i
      continue
    }
    if (runStart >= 0) {
      const len = i - runStart
      const shallow = deep < len && deep <= maxPause
      deep = 0
      if (shallow) {
        runStart = -1
        continue
      }
      const edge = runStart === 0 || i === samples.length
      const cap = edge ? (runStart === 0 ? maxLead : maxTrail) : maxPause
      if (len > cap) runs.push({ start: runStart, len, cap })
      runStart = -1
    }
  }

  const dropped = runs.reduce((n, r) => n + (r.len - r.cap), 0)
  if (dropped === 0) return buffer

  const outSamples = samples.length - dropped
  const out = new ArrayBuffer(HEADER + outSamples * 2)
  new Uint8Array(out).set(new Uint8Array(buffer, 0, HEADER))
  const outView = new DataView(out)
  // A header that disagrees with its payload decodes to noise.
  outView.setUint32(4, out.byteLength - 8, true)
  outView.setUint32(40, outSamples * 2, true)

  const dst = new Int16Array(out, HEADER, outSamples)
  let w = 0
  let read = 0
  for (const r of runs) {
    dst.set(samples.subarray(read, r.start), w)
    w += r.start - read
    // Digital zero spliced into a noise floor is heard as a dropout.
    dst.set(samples.subarray(r.start, r.start + r.cap), w)
    w += r.cap
    read = r.start + r.len
  }
  dst.set(samples.subarray(read), w)
  return out
}
