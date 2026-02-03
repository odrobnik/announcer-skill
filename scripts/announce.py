#!/usr/bin/env python3
"""Announce text through AirPlay speakers via Airfoil + ElevenLabs."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_PATH = Path.home() / "clawd" / "announcer" / "config.json"
ELEVENLABS_SPEECH = SKILL_DIR.parent / "elevenlabs" / "scripts" / "speech.py"

def load_config():
    """Load speaker config."""
    with open(CONFIG_PATH) as f:
        return json.load(f)

# Global constants from config
_config = load_config()
GONG_PATH = SKILL_DIR / "assets" / _config["audio"].get("chime_file", "gong_stereo.mp3")



def generate_tts(text: str, output_path: str, voice_id: str, fmt: str) -> bool:
    """Generate TTS audio via ElevenLabs."""
    cmd = [
        sys.executable, str(ELEVENLABS_SPEECH),
        text,
        "-v", voice_id,
        "-o", output_path,
        "--format", fmt
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"TTS error: {result.stderr}", file=sys.stderr)
        return False
    return True


def convert_to_stereo_mp3(input_path: str, output_path: str) -> bool:
    """Convert audio to stereo 48kHz MP3 for AirPlay compatibility."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ac", "2",  # stereo required for AirPlay
        "-ar", "48000",
        "-c:a", "libmp3lame",
        "-b:a", "256k",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def setup_airfoil(speakers: list[str], volume: float = 0.7) -> bool:
    """Set Airfoil source to System-Wide Audio, connect speakers, and set volume."""
    speaker_list = '", "'.join(speakers)
    script = f'''
    tell application "Airfoil"
        set current audio source to system source "System-Wide Audio"
        set targetSpeakers to {{"{speaker_list}"}}
        repeat with s in (every speaker)
            if (name of s) is in targetSpeakers then
                connect to s
                set (volume of s) to {volume}
            else
                disconnect from s
            end if
        end repeat
        return "ok"
    end tell
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def wait_for_connections(speakers: list[str], timeout: int = 30) -> bool:
    """Wait until all target speakers are connected and report which ones fail."""
    speaker_list = '", "'.join(speakers)
    
    # Script to get detailed connection status
    script = f'''
    tell application "Airfoil"
        set targetSpeakers to {{"{speaker_list}"}}
        set statusList to {{}}
        repeat with s in (every speaker)
            if (name of s) is in targetSpeakers then
                set end of statusList to (name of s) & "|" & (connected of s as text)
            end if
        end repeat
        return statusList
    end tell
    '''
    
    target_count = len(speakers)
    start = time.time()
    last_status = None
    
    while time.time() - start < timeout:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            try:
                # Parse output: ["Speaker1|true", "Speaker2|false", ...]
                status_lines = result.stdout.strip().split(", ")
                connected = []
                disconnected = []
                
                for line in status_lines:
                    if "|" in line:
                        speaker, is_connected = line.rsplit("|", 1)
                        if is_connected.lower() == "true":
                            connected.append(speaker)
                        else:
                            disconnected.append(speaker)
                
                connected_count = len(connected)
                
                # Only print update if status changed
                if connected_count != last_status:
                    print(f"  {connected_count}/{target_count} speakers connected...", file=sys.stderr)
                    last_status = connected_count
                
                # All connected!
                if connected_count >= target_count:
                    return True
                
            except (ValueError, AttributeError) as e:
                print(f"  Parse error: {e}", file=sys.stderr)
        
        time.sleep(1)
    
    # Timeout - report which speakers failed
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        try:
            status_lines = result.stdout.strip().split(", ")
            connected = []
            disconnected = []
            
            for line in status_lines:
                if "|" in line:
                    speaker, is_connected = line.rsplit("|", 1)
                    if is_connected.lower() == "true":
                        connected.append(speaker)
                    else:
                        disconnected.append(speaker)
            
            print(f"Warning: Only {len(connected)}/{target_count} speakers connected after {timeout}s", file=sys.stderr)
            if connected:
                print(f"  ✓ Connected: {', '.join(connected)}", file=sys.stderr)
            if disconnected:
                print(f"  ✗ Failed: {', '.join(disconnected)}", file=sys.stderr)
        except Exception as e:
            print(f"  Could not determine failed speakers: {e}", file=sys.stderr)
    
    return False


def play_audio(path: str):
    """Play audio through Terminal (picked up by Airfoil)."""
    subprocess.run(["afplay", path])


def disconnect_all():
    """Disconnect all speakers after announcement."""
    script = '''
    tell application "Airfoil"
        repeat with s in (every speaker)
            disconnect from s
        end repeat
    end tell
    '''
    subprocess.run(["osascript", "-e", script], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Announce text via AirPlay speakers")
    parser.add_argument("text", help="Text to announce")
    parser.add_argument("--speakers", help="Comma-separated speaker names (default: all configured)")
    parser.add_argument("--keep-connected", action="store_true", help="Don't disconnect after announcement")
    parser.add_argument("--no-gong", action="store_true", help="Skip the announcement gong")
    args = parser.parse_args()

    config = load_config()
    
    # Determine speakers
    if args.speakers:
        speakers = [s.strip() for s in args.speakers.split(",")]
    else:
        speakers = config["speakers"]
    
    # Use config voice_id, or fall back to Daniel (original voice)
    voice_id = config["elevenlabs"].get("voice_id", "onwK4e9ZLuTAKqWW03F9")
    fmt = config["elevenlabs"].get("format", "opus_48000_192")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        opus_path = os.path.join(tmpdir, "tts.opus")
        mp3_path = os.path.join(tmpdir, "tts.mp3")
        
        print(f"Generating TTS...", file=sys.stderr)
        if not generate_tts(args.text, opus_path, voice_id, fmt):
            sys.exit(1)
        
        print(f"Converting to stereo MP3...", file=sys.stderr)
        if not convert_to_stereo_mp3(opus_path, mp3_path):
            print("Conversion failed", file=sys.stderr)
            sys.exit(1)
        
        print(f"Setting up Airfoil ({len(speakers)} speakers)...", file=sys.stderr)
        volume = config["airfoil"].get("volume", 0.7)
        if not setup_airfoil(speakers, volume):
            print("Airfoil setup failed", file=sys.stderr)
            sys.exit(1)
        
        # Wait for all speakers to connect
        print(f"Waiting for connections...", file=sys.stderr)
        if not wait_for_connections(speakers):
            print("Warning: Not all speakers connected in time", file=sys.stderr)
        
        # Play gong before announcement
        if GONG_PATH.exists() and not args.no_gong:
            print(f"Playing gong...", file=sys.stderr)
            play_audio(str(GONG_PATH))
            time.sleep(0.3)  # Brief pause between gong and message
        
        print(f"Playing announcement...", file=sys.stderr)
        play_audio(mp3_path)
        
        if not args.keep_connected:
            # Wait for AirPlay buffer to flush before disconnecting
            time.sleep(3)
            disconnect_all()
    
    print("Done!", file=sys.stderr)


if __name__ == "__main__":
    main()
