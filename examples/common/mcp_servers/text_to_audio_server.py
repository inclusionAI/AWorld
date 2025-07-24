import numpy as np

from mcp.server import FastMCP
from pydantic import Field

from aworld.utils import import_package
from aworld.logs.util import logger

# Import required packages
import_package('gtts', install_name='gTTS')
import_package('pyttsx3', install_name='pyttsx3')
import_package('librosa', install_name='librosa')
import_package('soundfile', install_name='soundfile')
import pyttsx3
from gtts import gTTS
import librosa
import soundfile as sf

mcp = FastMCP("text_to_audio")

@mcp.tool()
def convert_text_to_audio(
    text: str = Field(description="Text to convert to audio"),
    output_file: str = Field(description="Path to the generated audio file")
) -> str:
    """Convert input text to audio with child-friendly settings.
    
    Args:
        text: Input text to convert
        output_file: Path to the generated audio file
    Returns:
        str: Path to the generated audio file
    """
    engine = pyttsx3.init()
    # Set default properties for child-friendly speech
    engine.setProperty('rate', 150)  # Slower speaking rate
    engine.setProperty('volume', 0.9)
    try:
        # Use default params if none provided
        params = {
            "speed": 0.9,
            "pitch": 1.1,
            "language": "en-US",
            "output_file": output_file,
            "use_gtts": True
        }

        # Preprocess text for child-friendly output
        text = _preprocess_text(text)
        if params.get("use_gtts", False):
            # Use gTTS for more natural sound
            tts = gTTS(text=text, lang=params["language"], slow=True)
            tts.save(params["output_file"])

        # Post-process audio if needed (adjust volume, remove noise, etc.)
        _post_process_audio(params["output_file"])
        return params["output_file"]

    except Exception as e:
        logger.error("Error in text-to-audio conversion: %s", str(e))
        raise

def _preprocess_text(text: str) -> str:
    """Preprocess text for child-friendly output.
    
    - Add pauses between sentences
    - Emphasize important words
    - Handle special characters
    """
    # Add slight pauses between sentences
    text = text.replace('. ', '... ')
    # Add emphasis on important words (can be customized)
    text = text.replace('!', '! ... ')
    return text

def _post_process_audio(audio_file: str) -> None:
    """Optimized post-processing for audio files."""
    try:
        # Load with a lower sample rate and mono channel
        y, sr = librosa.load(audio_file, sr=16000, mono=True)
        # Use faster normalization method
        y_norm = y / np.max(np.abs(y))
        # Write with optimized settings
        sf.write(
            audio_file,
            y_norm,
            sr,
            format='mp4',
            subtype='MP4'
        )
    except (IOError, ValueError, RuntimeError) as e:
        logger.warning("Audio post-processing failed: %s", e)

# Main function
if __name__ == "__main__":
    mcp.settings.port = 8888
    mcp.run(transport='sse')

    # text = "Hello, this is a test of the text-to-audio conversion."
    # output_file = "output1.mp4"
    # print(f"Converting text to audio: {text}")
    # audio_file = convert_text_to_audio(text, output_file)
    # print(f"Audio file saved to: {audio_file}")
