"""
LOQI — Central Configuration

All tunable settings in one place. Env vars loaded from .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Load .env from project root (if it exists)
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
AUDIO_SAMPLE_RATE = 16000       # Hz — required by faster-whisper & openWakeWord
AUDIO_CHANNELS = 1              # mono
AUDIO_SAMPLE_WIDTH = 2          # 16-bit = 2 bytes
AUDIO_CHUNK_MS = 30             # ms per VAD frame (10, 20, or 30)
AUDIO_CHUNK_SAMPLES = int(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS / 1000)  # 480
AUDIO_CHUNK_BYTES = AUDIO_CHUNK_SAMPLES * AUDIO_SAMPLE_WIDTH          # 960
MIC_DEVICE_INDEX = 15             # Nirvana Ion headset at 16kHz (None = system default)

# ---------------------------------------------------------------------------
# VAD (Voice Activity Detection)
# ---------------------------------------------------------------------------
VAD_AGGRESSIVENESS = 3          # 0 (least) to 3 (most aggressive)
VAD_SILENCE_TIMEOUT_MS = 1000   # stop recording after this much silence
VAD_MIN_SPEECH_MS = 300         # minimum speech duration before accepting

# ---------------------------------------------------------------------------
# STT (Speech-to-Text) — faster-whisper
# ---------------------------------------------------------------------------
STT_MODEL_SIZE = "base"         # "tiny" or "base" — both fast on CPU
STT_DEVICE = "cpu"              # "cpu" or "cuda"
STT_COMPUTE_TYPE = "int8"       # "int8" for low memory, "float16" for GPU

# ---------------------------------------------------------------------------
# TTS (Text-to-Speech) — Kokoro
# ---------------------------------------------------------------------------
TTS_LANG_CODE = "a"             # "a" = American English
TTS_VOICE = "af_heart"          # see Kokoro VOICES.md for options
TTS_SAMPLE_RATE = 24000         # Kokoro outputs 24kHz audio

# ---------------------------------------------------------------------------
# Wake Word — openWakeWord
# ---------------------------------------------------------------------------
WAKE_WORD_MODEL = "hey_loki.onnx" # Custom trained model
WAKE_WORD_THRESHOLD = 0.5       # lowered for better real-world detection       # detection confidence threshold
WAKE_WORD_CHUNK_SAMPLES = 1280  # 80ms at 16kHz — required by openWakeWord

# ---------------------------------------------------------------------------
# Groq (Cloud LLM Fallback)
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "qwen/qwen3.8-27b"
GROQ_BACKUP_MODEL = "openai/gpt-oss-20b"
GROQ_MAX_TOKENS = 1024
GROQ_TEMPERATURE = 0.7

# System prompt — short, direct, no markdown in responses (TTS can't read it)
GROQ_SYSTEM_PROMPT = """You are L.O.Q.I. (pronounced "Loki"), which stands for Local Operations & Query Interface.
You are a personal voice assistant running on the user's Windows PC.
If anyone asks your name, introduce yourself as L.O.Q.I. and explain what it stands for.
Rules:
- Keep responses concise and conversational — they'll be spoken aloud via TTS.
- Never use markdown formatting, bullet points, code blocks, or special characters.
- If asked to perform an action on the PC (open app, send message, delete file, etc.),
  describe what you'd do in plain language. Do NOT output code or shell commands.
- If you don't know something, say so briefly.
- Sound natural, like a helpful assistant, not a chatbot."""

# Conversation history — how many past turns to keep for context
GROQ_HISTORY_LENGTH = 10

# ---------------------------------------------------------------------------
# Permission Tiers (from jarvis_build_v2.md section 4)
# ---------------------------------------------------------------------------
# Tier 0: read-only/local          → no confirmation
# Tier 1: reversible action        → no confirmation
# Tier 2: sends/writes             → spoken confirm required
# Tier 3: destructive/irreversible → spoken confirm + repeat-back
# "never": unreviewed external content as command → always surface, never auto-execute

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
FALLBACK_LOG_PATH = LOGS_DIR / "fallback_log.jsonl"
