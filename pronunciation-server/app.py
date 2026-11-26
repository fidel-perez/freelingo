"""Phoneme recogniser service: audio in, list of IPA phones out."""
import io
import os
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import librosa
import numpy as np
import onnxruntime as ort
import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from lhotse.features.kaldi.extractors import Fbank, FbankConfig

MODEL_DIR = Path(os.environ.get("PHONEME_MODEL_DIR", "/models"))
MODEL_FILE = os.environ.get("PHONEME_MODEL_FILE", "model.onnx")
# Base URL; the service fetches model.onnx and tokens.txt from it when MODEL_DIR lacks them.
MODEL_URL = os.environ.get("PHONEME_MODEL_URL", "").rstrip("/")
SAMPLE_RATE = 16000
BLANK = 0

state: dict = {}


def fetch_weights() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name in (MODEL_FILE, "tokens.txt"):
        target = MODEL_DIR / name
        if target.exists():
            continue
        if not MODEL_URL:
            raise RuntimeError(f"{target} is missing and PHONEME_MODEL_URL is not set")
        urllib.request.urlretrieve(f"{MODEL_URL}/{name}", target)  # noqa: S310


def load_model() -> None:
    fetch_weights()
    vocab = {}
    for line in (MODEL_DIR / "tokens.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            vocab[int(parts[-1])] = parts[0]
    state["vocab"] = vocab
    state["session"] = ort.InferenceSession(
        str(MODEL_DIR / MODEL_FILE), providers=["CPUExecutionProvider"]
    )
    state["fbank"] = Fbank(FbankConfig(num_filters=80, dither=0.0, snip_edges=False))


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_model()
    yield


app = FastAPI(lifespan=lifespan)


def decode(data: bytes) -> list[str]:
    audio, rate = sf.read(io.BytesIO(data))
    if audio.ndim > 1:
        audio = audio[:, 0]
    if rate != SAMPLE_RATE:
        audio = librosa.resample(audio, orig_sr=rate, target_sr=SAMPLE_RATE)
    wave = torch.from_numpy(audio).float().unsqueeze(0)
    feats = state["fbank"].extract_batch([wave], sampling_rate=SAMPLE_RATE)[0]
    feats = feats.unsqueeze(0).numpy()
    inputs = {"x": feats, "x_lens": np.array([feats.shape[1]], dtype=np.int64)}
    ids = state["session"].run(None, inputs)[0][0].argmax(-1)
    phones, prev = [], -1
    for i in ids:
        if i != prev and i != BLANK:
            phones.append(state["vocab"].get(int(i), ""))
        prev = i
    return [p for p in phones if p and p != "▁"]


@app.get("/")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/phonemes")
async def phonemes(file: UploadFile = File(...), language: str = Form(...)) -> dict:  # noqa: B008
    # The recogniser is language-agnostic; `language` is required by the caller contract.
    try:
        return {"phonemes": decode(await file.read())}
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Audio could not be decoded") from exc
