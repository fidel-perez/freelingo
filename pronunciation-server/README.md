# Pronunciation server

This optional service turns a speech recording into a list of IPA phones. The recogniser is zipa, run as ONNX with CTC greedy decoding.

## Endpoints

- `GET /` answers 200 once the model is loaded.
- `POST /v1/phonemes` takes the multipart fields `file` (audio) and `language` (BCP 47 tag). It returns `{"phonemes": [...]}`.

## Weights

The image holds no weights. Mount a directory at `PHONEME_MODEL_DIR` (default `/models`) with `model.onnx` and `tokens.txt`. Alternatively, set `PHONEME_MODEL_URL` to a base URL, and the service downloads both files into that directory at start. `PHONEME_MODEL_FILE` renames the model file.

## Run

    docker build -t pronunciation-server pronunciation-server
    docker run -p 8000:8000 -v /path/to/models:/models pronunciation-server
