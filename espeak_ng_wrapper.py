"""Minimal espeak-ng CLI wrapper using the bundled espeakng_loader DLL."""
import sys
import os
import ctypes

# Get paths from espeakng_loader
import espeakng_loader
lib_path = espeakng_loader.get_library_path()
data_path = espeakng_loader.get_data_path()

# Set ESPEAK_DATA_PATH for the DLL
os.environ["ESPEAK_DATA_PATH"] = os.path.dirname(data_path)

# Load the DLL
espeak = ctypes.cdll.LoadLibrary(lib_path)

# Parse args to mimic: espeak-ng --ipa -q -v <voice> <text>
args = sys.argv[1:]
voice = "en-us"
text = ""
ipa_mode = False
quiet = False

i = 0
while i < len(args):
    if args[i] == "--ipa":
        ipa_mode = True
    elif args[i] == "-q":
        quiet = True
    elif args[i] == "-v" and i + 1 < len(args):
        i += 1
        voice = args[i]
    else:
        text = args[i]
    i += 1

if not text:
    sys.exit(0)

# Initialize espeak
AUDIO_OUTPUT_SYNCHRONOUS = 0x02
espeak.espeak_Initialize(AUDIO_OUTPUT_SYNCHRONOUS, 0, os.path.dirname(data_path).encode(), 0)
espeak.espeak_SetVoiceByName(voice.encode())

# Synthesize to get phonemes
if ipa_mode:
    # Use espeak_TextToPhonemes
    espeak.espeak_TextToPhonemes.restype = ctypes.c_char_p
    
    text_ptr = ctypes.c_char_p(text.encode("utf-8"))
    phoneme_mode = 0x02  # IPA
    
    result = espeak.espeak_TextToPhonemes(
        ctypes.byref(text_ptr),
        1,  # textmode: UTF-8
        phoneme_mode
    )
    if result:
        print(result.decode("utf-8"))
else:
    # Just speak (shouldn't be needed for training)
    espeak.espeak_Synth(
        text.encode("utf-8"),
        len(text) + 1,
        0, 0, 0, 0, None, None
    )
