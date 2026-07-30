"""
Camada de mídia: transcrição de áudio (STT) e síntese de fala (TTS).

Ambas rodam localmente dentro do container, sem depender de nenhuma API
paga da OpenAI (usamos apenas o login já existente do Codex CLI, que só
cobre texto e imagem). Isso mantém o custo em zero, ao preço de usar mais
CPU/RAM no container e de um tempo de "aquecimento" no primeiro uso.
"""
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from faster_whisper import WhisperModel

# Modelo carregado sob demanda (lazy) e mantido em memória entre chamadas.
_whisper_model: Optional[WhisperModel] = None

# "small" é um bom equilíbrio qualidade/velocidade/RAM para português.
# Se o container do Railway tiver pouca memória, troque para "base" ou "tiny".
WHISPER_MODEL_SIZE = "small"

PIPER_MODEL_PATH = "/opt/piper-voices/pt_BR-faber-medium.onnx"


def _get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
        )
    return _whisper_model


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """
    Transcreve áudio (qualquer formato que o ffmpeg reconheça: webm, mp3,
    wav, ogg...) para texto em português. Retorna string vazia se não
    conseguir identificar fala.
    """
    suffix = Path(filename).suffix or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()

        model = _get_whisper_model()
        segments, _info = model.transcribe(tmp.name, language="pt")
        text = " ".join(segment.text.strip() for segment in segments)

    return text.strip()


def synthesize_speech(text: str) -> bytes:
    """
    Gera áudio WAV a partir de texto, usando o Piper (TTS local, offline,
    voz em português pt_BR). Retorna os bytes do arquivo .wav.
    """
    if not text.strip():
        raise ValueError("Texto vazio: nada para sintetizar.")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        proc = subprocess.run(
            ["piper", "--model", PIPER_MODEL_PATH, "--output_file", tmp.name],
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Piper (TTS) falhou: {proc.stderr.decode(errors='replace')}"
            )
        return Path(tmp.name).read_bytes()
