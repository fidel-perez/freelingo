import { capSilence } from '@/lib/cap-silence'
import type { PictureVoice } from '@/lib/picture-talk'

export interface PictureAudioQueue {
  enqueue: (buffer: ArrayBuffer, voice: PictureVoice) => void
  cancel: () => void
  dispose: () => void
}

function isWav(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 12) return false
  const view = new DataView(buffer)
  return view.getUint32(0) === 0x52494646 && view.getUint32(8) === 0x57415645
}

// Only an `<audio>` element keeps pitch at a faster rate. The first native
// element stays in the page so a browser test reads it.
export function createPictureAudioQueue({
  nativeRate,
  onIdle,
}: {
  nativeRate: () => number
  onIdle?: () => void
}): PictureAudioQueue {
  const pending: { buffer: ArrayBuffer; voice: PictureVoice }[] = []
  let current: { audio: HTMLAudioElement; finish: () => void } | null = null
  let nativeShown: HTMLAudioElement | null = null
  let generation = 0

  function mark(audio: HTMLAudioElement, state: string) {
    if (audio === nativeShown) audio.setAttribute('data-native-audio', state)
  }

  function playNext() {
    if (current) return
    const item = pending.shift()
    if (!item) {
      onIdle?.()
      return
    }
    const token = generation
    const native = item.voice === 'native'
    const bytes = native ? capSilence(item.buffer) : item.buffer
    const url = URL.createObjectURL(
      new Blob([bytes], { type: isWav(bytes) ? 'audio/wav' : 'audio/mpeg' })
    )
    const audio = new Audio(url)
    if (native) {
      audio.preservesPitch = true
      audio.playbackRate = nativeRate()
      if (!nativeShown) {
        nativeShown = audio
        audio.style.display = 'none'
        document.body.appendChild(audio)
        mark(audio, 'loading')
      }
    } else {
      audio.playbackRate = 1
    }
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      audio.onended = audio.onerror = null
      URL.revokeObjectURL(url)
      current = null
      if (token === generation) playNext()
    }
    audio.onplaying = () => mark(audio, 'playing')
    audio.onended = () => {
      mark(audio, 'ended')
      finish()
    }
    audio.onerror = () => {
      mark(audio, 'failed')
      finish()
    }
    current = { audio, finish }
    void audio.play().catch(() => {
      mark(audio, 'failed')
      finish()
    })
  }

  const queue: PictureAudioQueue = {
    enqueue(buffer, voice) {
      pending.push({ buffer, voice })
      playNext()
    },
    cancel() {
      generation += 1
      pending.length = 0
      const playing = current
      if (!playing) return
      playing.audio.pause()
      mark(playing.audio, 'cancelled')
      playing.finish()
    },
    dispose() {
      queue.cancel()
      nativeShown?.remove()
      nativeShown = null
    },
  }
  return queue
}
