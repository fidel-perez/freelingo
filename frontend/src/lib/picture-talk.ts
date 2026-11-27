import type { User } from '@/store/auth'

export const DEFAULT_NATIVE_SPEECH_RATE = 1.8

export interface PictureScene {
  id: number
  study_plan_id: number
  image_prompt: string
  words: string[]
}

export type PictureVoice = 'study' | 'native'

/** The catalog entry: the learner opted into images and weights are set. A browser without WebGPU still talks, with the scene's words in place of a picture. */
export function pictureTalkAvailable(user: User | null | undefined): boolean {
  return !!user?.images_enabled && !!user.image_model_base_url
}
