/* eslint-disable @typescript-eslint/ban-ts-comment */
// @ts-nocheck
// Jeveration text-to-image on onnxruntime-web WebGPU: one backbone pass, 128 codes, TA-TiTok
// decoder, 512x512 image. Ported from the Jeveration-WebGPU Space (bench/image/jeveration.js).
import { Tokenizer } from '@huggingface/tokenizers'

const N_CODES = 128,
  CODE_DIM = 64,
  K = 8192,
  HIDDEN = 2560,
  CLIP_LEN = 77

/** Small seeded RNG (xoshiro128**) with uniform and normal draws. */
export class Rng {
  constructor(seed = 0) {
    let s = seed >>> 0 || 1
    const sm = () => {
      s = (s + 0x9e3779b9) >>> 0
      let z = s
      z = Math.imul(z ^ (z >>> 16), 0x85ebca6b) >>> 0
      z = Math.imul(z ^ (z >>> 13), 0xc2b2ae35) >>> 0
      return (z ^ (z >>> 16)) >>> 0
    }
    this.a = sm()
    this.b = sm()
    this.c = sm()
    this.d = sm()
  }
  u32() {
    const r =
        Math.imul(
          (Math.imul(this.b, 5) << 7) | (Math.imul(this.b, 5) >>> 25),
          9
        ) >>> 0,
      t = this.b << 9
    this.c ^= this.a
    this.d ^= this.b
    this.b ^= this.c
    this.a ^= this.d
    this.c ^= t
    this.d = (this.d << 11) | (this.d >>> 21)
    return r
  }
  uniform() {
    return (this.u32() + 0.5) / 4294967296
  }
  normal() {
    return (
      Math.sqrt(-2 * Math.log(this.uniform())) *
      Math.cos(2 * Math.PI * this.uniform())
    )
  }
}

export class JeverationPipeline {
  /** files: {bytes(path, onProgress), json(path)}; graphs and assets as published in the weights folder */
  static async load(
    files,
    { ort, device = 'webgpu', head = 'head_fp16.onnx', progress } = {}
  ) {
    const p = new JeverationPipeline()
    p.ort = ort
    p.device = device
    const S = async (path, { gpuOut = false } = {}) => {
      const opts = {
        executionProviders: [device === 'webgpu' ? 'webgpu' : 'cpu'],
        graphOptimizationLevel: 'all',
      }
      if (device === 'webgpu' && gpuOut)
        opts.preferredOutputLocation = 'gpu-buffer'
      progress?.({ status: 'loading', file: path })
      const onP = (e) => progress?.({ status: 'progress', ...e })
      opts.externalData = [
        {
          path: path.split('/').pop() + '_data',
          data: await files.bytes(path + '_data', onP),
        },
      ]
      return ort.InferenceSession.create(await files.bytes(path, onP), opts)
    }
    p.tpl = await files.json('assets/prompt_template.json')
    p.sampling = await files.json('assets/sampling.json')
    const f32file = async (f) => {
      const b = await files.bytes(f)
      return new Float32Array(
        b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)
      )
    }
    p.codebookStd = await f32file('assets/codebook_std.bin')
    p.maskEmb = await f32file('assets/mask_emb.bin')
    p.qwenTok = new Tokenizer(
      await files.json('tokenizer/qwen/tokenizer.json'),
      await files.json('tokenizer/qwen/tokenizer_config.json')
    )
    p.clipTok = new Tokenizer(
      await files.json('tokenizer/clip/tokenizer.json'),
      await files.json('tokenizer/clip/tokenizer_config.json')
    )
    const manifest = await files.json('backbone_int8/manifest.json')
    p.s = {
      embed: await S('embed_tokens_q4f16.onnx'),
      decisionEmbed: await S('decision_embed_fp16.onnx'),
      chunks: [],
      head: await S(head),
      clip: await S('clip_text_fp16.onnx'),
      decoder: await S('decoder_fp16.onnx'),
    }
    for (const [i, f] of manifest.files.entries())
      p.s.chunks.push(await S(f, { gpuOut: i < manifest.files.length - 1 }))
    return p
  }

  /** prompt -> backbone token ids: chat head + prompt (<= 128 tokens) + chat tail */
  promptIds(prompt) {
    const ids = this.qwenTok
      .encode(prompt, { add_special_tokens: false })
      .ids.slice(0, this.tpl.max_prompt_tokens)
    return [...this.tpl.head_ids, ...ids, ...this.tpl.tail_ids]
  }

  /** prompt -> 77 CLIP ids, padded with 0 after the end token (open_clip's convention) */
  clipIds(prompt) {
    const ids = this.clipTok.encode(
      prompt.toLowerCase().trim().replace(/\s+/g, ' ')
    ).ids
    const out = new BigInt64Array(CLIP_LEN)
    const body = ids.slice(0, CLIP_LEN)
    if (body.length === CLIP_LEN) body[CLIP_LEN - 1] = ids[ids.length - 1]
    body.forEach((v, i) => {
      out[i] = BigInt(v)
    })
    return out
  }

  /** each position masked (mask_emb) with prob MASK_PROB or a random standardised code, plus noise */
  randomState(rng) {
    const { mask_prob: rho, state_noise: sigma } = this.sampling
    const state = new Float32Array(N_CODES * CODE_DIM)
    let masked = 0
    const isMasked = [],
      code = []
    for (let i = 0; i < N_CODES; i++) isMasked.push(rng.uniform() < rho)
    for (let i = 0; i < N_CODES; i++) code.push(rng.u32() % K)
    const a = Math.sqrt(1 - sigma * sigma)
    for (let i = 0; i < N_CODES; i++) {
      if (isMasked[i]) masked++
      for (let j = 0; j < CODE_DIM; j++) {
        const v = isMasked[i]
          ? this.maskEmb[j]
          : this.codebookStd[code[i] * CODE_DIM + j]
        state[i * CODE_DIM + j] = a * v + sigma * rng.normal()
      }
    }
    return { state, t: new Float32Array([masked / N_CODES]) }
  }

  /** Gumbel noise [128*8192]: one categorical draw per position is argmax(logits + noise) */
  static gumbel(rng) {
    const g = new Float32Array(N_CODES * K)
    for (let i = 0; i < g.length; i++)
      g[i] = -Math.log(-Math.log(rng.uniform()))
    return g
  }

  static sampleCodes(logits, noise) {
    const codes = new BigInt64Array(N_CODES)
    for (let i = 0; i < N_CODES; i++) {
      let best = -Infinity,
        arg = 0
      for (let k = 0, o = i * K; k < K; k++) {
        const v = logits[o + k] + noise[o + k]
        if (v > best) {
          best = v
          arg = k
        }
      }
      codes[i] = BigInt(arg)
    }
    return codes
  }

  /** Everything a seed decides, computed once: random state, decision-token embeddings, sampling noise. */
  async seedCache(seed) {
    if (this._seed?.seed === seed) return this._seed
    const rng = new Rng(seed),
      { state, t } = this.randomState(rng),
      noise = JeverationPipeline.gumbel(rng)
    const T = (n, d, dims) => new this.ort.Tensor(n, d, dims)
    const tail = toF32(
      (
        await this.s.decisionEmbed.run({
          state: T('float32', state, [1, N_CODES, CODE_DIM]),
          t: T('float32', t, [1]),
        })
      ).tail.data
    )
    return (this._seed = { seed, state, t, noise, tail })
  }

  /** backbone pass and image head: (ids, state, t) -> logits Float32Array [128*8192] */
  async logits(ids, state, t, tailCached = null) {
    const { ort, s } = this,
      T = (n, d, dims) => new ort.Tensor(n, d, dims)
    const emb = (
      await s.embed.run({
        input_ids: T('int64', BigInt64Array.from(ids.map(BigInt)), [
          1,
          ids.length,
        ]),
      })
    ).inputs_embeds
    const tail =
      tailCached ??
      toF32(
        (
          await s.decisionEmbed.run({
            state: T('float32', state, [1, N_CODES, CODE_DIM]),
            t: T('float32', t, [1]),
          })
        ).tail.data
      )
    const L = ids.length + N_CODES,
      x = new Float32Array(L * HIDDEN)
    x.set(toF32(emb.data), 0)
    x.set(tail, ids.length * HIDDEN)
    const pos = new BigInt64Array(3 * L)
    for (let a = 0; a < 3; a++)
      for (let i = 0; i < L; i++) pos[a * L + i] = BigInt(i)
    const position_ids = T('int64', pos, [3, 1, L])
    let h = T('float32', x, [1, L, HIDDEN])
    for (const c of s.chunks) {
      const o = await c.run({ hidden_in: h, position_ids })
      if (h.location === 'gpu-buffer') h.dispose()
      h = o.hidden_out ?? o.hidden
    }
    const hidden = h
    const lg = (
      await s.head.run({
        hidden,
        state: T('float32', state, [1, N_CODES, CODE_DIM]),
        t: T('float32', t, [1]),
      })
    ).logits
    if (hidden.location === 'gpu-buffer') hidden.dispose()
    return toF32(lg.location === 'gpu-buffer' ? await lg.getData() : lg.data)
  }

  /** codes + prompt -> RGB uint8 [512*512*3] */
  async decode(codes, prompt) {
    const { ort, s } = this
    const tg = (
      await s.clip.run({
        clip_ids: new ort.Tensor('int64', this.clipIds(prompt), [1, CLIP_LEN]),
      })
    ).text_guidance
    const img = (
      await s.decoder.run({
        codes: new ort.Tensor('int64', codes, [1, N_CODES]),
        text_guidance: tg,
      })
    ).image.data
    const hw = 512 * 512,
      rgb = new Uint8Array(hw * 3)
    for (let c = 0; c < 3; c++)
      for (let i = 0; i < hw; i++)
        rgb[i * 3 + c] = Math.round(
          Math.min(1, Math.max(0, img[c * hw + i])) * 255
        )
    return rgb
  }

  /** prompt -> RGB uint8 [512*512*3]; one backbone pass. Same seed, same random state and noise. */
  async generate(prompt, { seed = 0 } = {}) {
    const c = await this.seedCache(seed)
    const logits = await this.logits(
      this.promptIds(prompt),
      c.state,
      c.t,
      c.tail
    )
    const codes = JeverationPipeline.sampleCodes(logits, c.noise)
    return this.decode(codes, prompt)
  }
}

function toF32(d) {
  if (d instanceof Float32Array) return d
  if (d instanceof Uint16Array) {
    const o = new Float32Array(d.length)
    for (let i = 0; i < d.length; i++) o[i] = half(d[i])
    return o
  }
  return Float32Array.from(d)
}
function half(h) {
  const s = h & 0x8000 ? -1 : 1,
    e = (h >> 10) & 0x1f,
    f = h & 0x3ff
  if (e === 0) return s * 2 ** -14 * (f / 1024)
  if (e === 31) return f ? NaN : s * Infinity
  return s * 2 ** (e - 15) * (1 + f / 1024)
}
