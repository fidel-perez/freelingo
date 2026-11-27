import { apiFetch } from '@/lib/api'
import { resolveConfiguredTtsVoice } from '@/lib/tts-voice'

async function postLine<T>(
  path: string,
  text: string,
  studyPlanId: number
): Promise<T> {
  const res = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, study_plan_id: studyPlanId }),
  })
  if (!res.ok) throw new Error(`Tutor request failed: ${res.status}`)
  return (await res.json()) as T
}

/** The line in the learner's native language (POST /api/tutor/translate). */
export async function translateLine(
  text: string,
  studyPlanId: number
): Promise<string> {
  const data = await postLine<{ translation: string }>(
    '/api/tutor/translate',
    text,
    studyPlanId
  )
  return data.translation
}

/** Log the line in the mistake log; true when a flashcard was made or restarted. */
export async function flagMistake(
  text: string,
  studyPlanId: number
): Promise<boolean> {
  const data = await postLine<{ logged: boolean }>(
    '/api/tutor/flag-mistake',
    text,
    studyPlanId
  )
  return data.logged
}

let current: { audio: HTMLAudioElement; url: string } | null = null

function stopCurrent() {
  if (!current) return
  current.audio.pause()
  URL.revokeObjectURL(current.url)
  current = null
}

/** Speak one line through POST /api/tts; a new replay stops the previous one. */
export async function speakLine(
  text: string,
  studyPlanId: number
): Promise<void> {
  stopCurrent()
  const voice = await resolveConfiguredTtsVoice(new AbortController().signal)
  const res = await apiFetch('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice, study_plan_id: studyPlanId }),
  })
  if (!res.ok) throw new Error(`TTS error ${res.status}`)
  const url = URL.createObjectURL(await res.blob())
  const audio = new Audio(url)
  stopCurrent()
  current = { audio, url }
  audio.onended = () => {
    if (current?.audio === audio) stopCurrent()
  }
  await audio.play()
}
