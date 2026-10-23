FROM python:3.14-slim

WORKDIR /code

# Model cache lives outside /code so the compose bind mount (.:/code) does not hide it.
ENV HF_HOME=/opt/hf-cache

COPY requirements-torch.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-torch.txt \
 && pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model at build time (pinned revision, PyTorch
# safetensors weights only - the repo's ONNX/OpenVINO/TF copies are skipped).
ARG EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ARG EMBED_REVISION=e8f8c211226b894fcb81acc59f3b34ba3efd5f42
RUN python -c "from huggingface_hub import snapshot_download; \
snapshot_download('${EMBED_MODEL}', revision='${EMBED_REVISION}', \
ignore_patterns=['onnx/*', 'openvino/*', 'pytorch_model.bin', 'tf_model.h5', 'README.md'])"

# At runtime, load only from the local cache - no network calls to the Hub.
ENV HF_HUB_OFFLINE=1 \
    EMBED_MODEL=${EMBED_MODEL} \
    EMBED_REVISION=${EMBED_REVISION}

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
