# main.py
import os
import requests
import json
import subprocess
import whisper
import tempfile
import asyncio
import uuid
import shutil
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip, concatenate_videoclips, AudioFileClip
import imageio_ffmpeg as ffmpeg
import numpy as np
import threading

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request
import asyncio
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

from dotenv import load_dotenv
load_dotenv()

# === CONFIGURATION ===
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("VOICE_ID", "KUJ0dDUYhYz8c1Is7Ct6")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny")  # tiny|base|small (tiny uses least memory)
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "1"))  # serialize by default

# Validate required environment variables
if not PEXELS_API_KEY:
    raise ValueError("PEXELS_API_KEY environment variable is required")
if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY environment variable is required")

MIN_CLIPS = 3
MAX_CLIPS = 10

# Create directories
TEMP_DIR = Path("temp_videos")
OUTPUT_DIR = Path("output_videos")
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Concurrency limiter (keep memory low by processing one job at a time)
TASK_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# === PYDANTIC MODELS ===
class VideoGenerationRequest(BaseModel):
    script_text: str = Field(..., description="The script text for voiceover", min_length=10)
    search_query: str = Field(default="technology", description="Search query for relevant video clips")
    font_size: int = Field(default=120, ge=60, le=200, description="Font size for captions")
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID (optional)")
    voice_settings: Optional[Dict[str, Any]] = Field(default=None, description="Custom voice settings")
    callback_url: Optional[str] = Field(default=None, description="If provided, the server will POST the generated video to this URL when done.")

class VideoGenerationResponse(BaseModel):
    task_id: str
    status: str
    message: str
    estimated_duration: Optional[float] = None

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str  # "pending", "processing", "completed", "failed"
    progress: Optional[str] = None
    error: Optional[str] = None
    output_file: Optional[str] = None
    duration: Optional[float] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    logs: Optional[List[str]] = None

# === GLOBAL TASK STORAGE ===
tasks: Dict[str, Dict[str, Any]] = {}

def log_task(task_id: str, message: str) -> None:
    """Append a timestamped log line to the task's log buffer and mirror to progress."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {message}"
        if task_id in tasks:
            tasks[task_id].setdefault('logs', []).append(line)
            # Also mirror last message to progress for quick view
            tasks[task_id]['progress'] = message
        print(line)
    except Exception as e:
        print(f"[log_task error] {e}")

# === FASTAPI APP ===
app = FastAPI(
    title="AI Video Generator API",
    description="Generate short-form videos with voiceover, multiple video clips, and smart captions",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cryptopilotaiclient.onrender.com",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure bundled ffmpeg/ffprobe are discoverable by subprocess callers (e.g., whisper)
try:
    _ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
    _ffmpeg_dir = os.path.dirname(_ffmpeg_exe)
    os.environ["IMAGEIO_FFMPEG_EXE"] = _ffmpeg_exe
    os.environ["PATH"] = f"{_ffmpeg_dir}{os.pathsep}{os.environ.get('PATH','')}"
    # On Windows, also place a local ffmpeg.exe so tools that call "ffmpeg" by name can find it
    try:
        from pathlib import Path as _Path
        import shutil as _shutil
        _dst = _Path("ffmpeg.exe")
        if os.name == "nt" and not _dst.exists() and _ffmpeg_exe.lower().endswith("ffmpeg.exe"):
            _shutil.copy2(_ffmpeg_exe, str(_dst))
    except Exception as _copy_e:
        print(f"Warning: could not place local ffmpeg.exe copy: {_copy_e}")
    print(f"Using bundled ffmpeg at: {_ffmpeg_exe}")
except Exception as _e:
    print(f"Warning: could not set bundled ffmpeg PATH: {_e}")

# === REQUEST LOGGING MIDDLEWARE ===
@app.middleware("http")
async def log_requests(request: Request, call_next):
    try:
        method = request.method
        url = str(request.url)
        headers = {k: v for k, v in request.headers.items()}
        query_params = dict(request.query_params)

        # Read body safely and re-inject so downstream can read it
        body_bytes = await request.body()
        try:
            body_preview = body_bytes.decode("utf-8", errors="replace")
        except Exception:
            body_preview = str(body_bytes)

        print("=== Incoming Request ===")
        print(f"Method: {method}")
        print(f"URL: {url}")
        if query_params:
            print(f"Query: {query_params}")
        print(f"Headers: {headers}")
        if body_bytes:
            print(f"Body: {body_preview}")
        else:
            print("Body: <empty>")
        print("========================")

        async def receive_gen():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        # Call next with a Request that can still consume the body
        response = await call_next(Request(request.scope, receive=receive_gen))
        return response
    except Exception as e:
        print(f"[Request Logging Error] {e}")
        return await call_next(request)
# === END REQUEST LOGGING MIDDLEWARE ===

# === VIDEO PROCESSING FUNCTIONS ===
async def search_multiple_pexels_videos(query: str, num_clips: int = 5):
    """Fetch multiple video clips from Pexels"""
    print(f"Searching for {num_clips} video clips with query: '{query}'")
    
    video_urls = []
    page = 1
    per_page = min(num_clips, 15)
    
    while len(video_urls) < num_clips and page <= 3:
        url = f"https://api.pexels.com/videos/search?query={query}&per_page={per_page}&page={page}"
        headers = {"Authorization": PEXELS_API_KEY}
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to fetch from Pexels API: {str(e)}")
        
        data = response.json()
        
        if not data.get('videos'):
            break
            
        for video in data['videos']:
            if len(video_urls) >= num_clips:
                break
            video_files = sorted(video['video_files'], key=lambda x: x.get('width', 0), reverse=True)
            if video_files:
                video_urls.append({
                    'url': video_files[0]['link'],
                    'duration': video.get('duration', 10),
                    'id': video['id']
                })
        
        page += 1
    
    if not video_urls:
        raise Exception(f"No videos found for query: {query}")
    
    return video_urls

async def download_multiple_videos(video_data_list, task_id: str):
    """Download multiple videos and return their file paths"""
    video_paths = []
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(exist_ok=True)
    
    for i, video_data in enumerate(video_data_list):
        video_path = task_dir / f"pexels_video_{i+1}.mp4"
        
        try:
            with requests.get(video_data['url'], stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(video_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
        except requests.exceptions.RequestException as e:
            print(f"Failed to download video {i+1}: {e}")
            continue
        
        video_paths.append({
            'path': str(video_path),
            'duration': video_data['duration'],
            'id': video_data['id']
        })
        
        # Update progress
        tasks[task_id]['progress'] = f"Downloaded video {i+1}/{len(video_data_list)}"
    
    if not video_paths:
        raise Exception("Failed to download any videos")
    
    return video_paths

def convert_to_shorts(input_path: str, output_path: str):
    """Convert a single video to shorts format using bundled ffmpeg"""
    ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg_exe, "-y", "-i", input_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:a", "copy", output_path
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
        return True
    except subprocess.TimeoutExpired:
        raise Exception(f"FFmpeg timeout while converting {input_path}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"FFmpeg error: {e.stderr}")

async def convert_multiple_to_shorts(video_paths, task_id: str):
    """Convert multiple videos to shorts format"""
    converted_paths = []
    task_dir = TEMP_DIR / task_id
    
    for i, video_data in enumerate(video_paths):
        output_path = task_dir / f"shorts_video_{i+1}.mp4"
        try:
            convert_to_shorts(video_data['path'], str(output_path))
            converted_paths.append({
                'path': str(output_path),
                'duration': video_data['duration'],
                'id': video_data['id']
            })
        except Exception as e:
            print(f"Failed to convert video {i+1}: {e}")
            continue
        
        # Update progress
        tasks[task_id]['progress'] = f"Converted video {i+1}/{len(video_paths)} to shorts format"
    
    if not converted_paths:
        raise Exception("Failed to convert any videos to shorts format")
    
    return converted_paths

async def generate_voiceover(script_text: str, task_id: str, voice_id: str = None, voice_settings: Dict = None):
    """Generate voiceover from script text with enhanced error handling"""
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(exist_ok=True)  # Ensure directory exists
    output_file = task_dir / "voiceover.mp3"
    
    # Use provided voice_id or default
    current_voice_id = voice_id or VOICE_ID
    
    # Default voice settings
    default_voice_settings = {
        "stability": 0.75,
        "similarity_boost": 0.75,
        "style": 0.2,
        "use_speaker_boost": True
    }
    
    # Merge with custom settings if provided
    if voice_settings:
        default_voice_settings.update(voice_settings)
    
    # Create SSML
    ssml_text = f"""
    <speak>
      <prosody rate="90%" pitch="+2st">
        {script_text.strip()}
      </prosody>
    </speak>
    """
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{current_voice_id}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    payload = {
        "text": ssml_text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": default_voice_settings,
        "text_type": "ssml"
    }
    
    try:
        print(f"Making request to ElevenLabs API for task {task_id}")
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        
        if response.status_code == 200:
            with open(output_file, "wb") as f:
                f.write(response.content)
            
            # Verify file was created and has content
            if not output_file.exists():
                raise Exception("Voiceover file was not created")
            
            if output_file.stat().st_size == 0:
                raise Exception("Voiceover file is empty")
                
            print(f"Voiceover created successfully: {output_file}")
            return str(output_file)
        else:
            error_msg = f"ElevenLabs API failed: {response.status_code} - {response.text}"
            print(error_msg)
            raise Exception(error_msg)
            
    except requests.exceptions.RequestException as e:
        error_msg = f"Network error calling ElevenLabs API: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error in voiceover generation: {str(e)}"
        print(error_msg)
        raise Exception(error_msg)

def get_audio_duration(audio_path: str) -> float:
    """Get the duration of an audio file using bundled ffmpeg"""
    try:
        ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
        command = [
            ffmpeg_exe, "-i", audio_path, "-f", "null", "-"
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        
        # ffmpeg outputs duration info to stderr
        stderr_output = result.stderr
        
        # Look for duration in format "Duration: HH:MM:SS.ss"
        import re
        duration_match = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})', stderr_output)
        if not duration_match:
            raise Exception("Could not parse duration from ffmpeg output")
        
        hours, minutes, seconds, centiseconds = map(int, duration_match.groups())
        duration = hours * 3600 + minutes * 60 + seconds + centiseconds / 100.0
        
        if duration <= 0:
            raise Exception("Invalid audio duration")
            
        return duration
    except Exception as e:
        raise Exception(f"Failed to get audio duration: {str(e)}")

async def create_seamless_video_compilation(video_paths, target_duration: float, task_id: str):
    """Create a seamless compilation of multiple videos to match target duration"""
    task_dir = TEMP_DIR / task_id
    output_path = task_dir / "compiled_video.mp4"
    
    clips = []
    current_duration = 0
    video_index = 0
    
    # Load all video clips
    loaded_videos = []
    for video_data in video_paths:
        try:
            clip = VideoFileClip(video_data['path'])
            if clip.duration > 0:  # Ensure valid duration
                loaded_videos.append(clip)
            else:
                clip.close()
        except Exception as e:
            print(f"Error loading video {video_data['path']}: {e}")
            continue
    
    if not loaded_videos:
        raise Exception("No videos could be loaded successfully")
    
    # Create compilation
    while current_duration < target_duration:
        remaining_time = target_duration - current_duration
        current_clip = loaded_videos[video_index % len(loaded_videos)]
        
        if current_clip.duration <= remaining_time:
            clip_to_add = current_clip.copy()
            duration_to_add = current_clip.duration
        else:
            clip_to_add = current_clip.subclip(0, remaining_time)
            duration_to_add = remaining_time
        
        clips.append(clip_to_add)
        current_duration += duration_to_add
        video_index += 1
        
        # Update progress
        tasks[task_id]['progress'] = f"Compiling video: {current_duration:.1f}/{target_duration:.1f}s"
    
    try:
        # Concatenate clips
        final_video = concatenate_videoclips(clips, method="compose")
        final_video.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(task_dir / "temp-audio-compile.m4a"),
            remove_temp=True,
            verbose=False,
            logger=None
        )
        
        # Cleanup
        for clip in clips:
            clip.close()
        for video in loaded_videos:
            video.close()
        final_video.close()
        
        return str(output_path)
    except Exception as e:
        # Cleanup on error
        for clip in clips:
            try:
                clip.close()
            except:
                pass
        for video in loaded_videos:
            try:
                video.close()
            except:
                pass
        raise Exception(f"Failed to create video compilation: {str(e)}")

def merge_voiceover(video_path: str, audio_path: str, output_path: str):
    """Merge voiceover with video using bundled ffmpeg"""
    ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg_exe, "-y", "-i", video_path, "-i", audio_path,
        "-c:v", "libx264", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", 
        "-shortest", output_path
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg timeout while merging audio and video")
    except subprocess.CalledProcessError as e:
        raise Exception(f"FFmpeg error during audio merge: {e.stderr}")

def extract_words_with_timestamps(whisper_result):
    """Extract all words with timestamps from Whisper result"""
    words = []
    for segment in whisper_result["segments"]:
        if "words" in segment:
            for word in segment["words"]:
                words.append({
                    "word": word["word"].strip(),
                    "start": word["start"],
                    "end": word["end"]
                })
    return words

def create_smart_word_groups(words):
    """Group words respecting punctuation breaks"""
    groups = []
    current_group = []
    
    for word in words:
        current_group.append(word)
        word_text = word["word"].strip()
        
        has_break_punct = any(punct in word_text for punct in ['.', '!', '?', ',', ';', ':'])
        
        if has_break_punct or len(current_group) >= 3:
            groups.append(current_group.copy())
            current_group = []
    
    if current_group:
        groups.append(current_group)
    
    return groups

def create_caption_clips(words, video_size, font_size=120):
    """Create caption clips with smart word grouping"""
    clips = []
    w, h = video_size
    
    # Load font
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", font_size)
        except:
            try:
                # Windows default fonts
                font = ImageFont.truetype("arial.ttf", font_size)
            except:
                font = ImageFont.load_default()
    
    word_groups = create_smart_word_groups(words)
    
    for word_group in word_groups:
        for word_idx, word in enumerate(word_group):
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            words_text = [w["word"] for w in word_group]
            y_position = h - int(h * 0.3) - (font_size // 2)  # Moved up slightly
            
            full_text = " ".join(words_text)
            total_width = draw.textlength(full_text, font=font)
            start_x = max(20, (w - total_width) // 2)  # More margin
            current_x = start_x
            
            for i, word_text in enumerate(words_text):
                color = "#FFD700" if i == word_idx else "#FFFFFF"  # Gold highlight, white text
                
                # Add thicker stroke for better readability
                stroke_width = 3
                for dx in range(-stroke_width, stroke_width + 1):
                    for dy in range(-stroke_width, stroke_width + 1):
                        if dx != 0 or dy != 0:
                            draw.text((current_x + dx, y_position + dy), word_text, 
                                     font=font, fill="#000000")  # Black stroke
                
                draw.text((current_x, y_position), word_text, font=font, fill=color)
                
                word_width = draw.textlength(word_text, font=font)
                space_width = draw.textlength(" ", font=font)
                current_x += word_width + space_width
            
            img_array = np.array(img)
            clip = ImageClip(img_array, duration=word["end"] - word["start"])
            clip = clip.set_start(word["start"]).set_position("center")
            clips.append(clip)
    
    return clips

async def add_captions_to_video(video_path: str, words, font_size: int, task_id: str):
    """Add captions to video"""
    task_dir = TEMP_DIR / task_id
    output_path = OUTPUT_DIR / f"{task_id}_final.mp4"
    
    video = None
    final_video = None
    caption_clips = []
    
    try:
        video = VideoFileClip(video_path)
        caption_clips = create_caption_clips(words, video.size, font_size)
        
        final_video = CompositeVideoClip([video] + caption_clips)
        final_video.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(task_dir / "temp-audio-final.m4a"),
            remove_temp=True,
            verbose=False,
            logger=None
        )
        
        return str(output_path)
    
    except Exception as e:
        raise Exception(f"Failed to add captions: {str(e)}")
    
    finally:
        # Cleanup
        if video:
            video.close()
        if final_video:
            final_video.close()
        for clip in caption_clips:
            try:
                clip.close()
            except:
                pass

def calculate_required_clips(target_duration: float, average_clip_duration: float = 15.0) -> int:
    """Calculate required number of clips"""
    estimated_clips = int(target_duration / average_clip_duration) + 2
    return max(MIN_CLIPS, min(MAX_CLIPS, estimated_clips))

async def process_video_generation(request: VideoGenerationRequest, task_id: str):
    """Main video processing function with enhanced error handling"""
    try:
        tasks[task_id]['status'] = 'processing'
        tasks[task_id]['progress'] = 'Starting video generation...'
        
        # Validate API keys
        if not ELEVENLABS_API_KEY or ELEVENLABS_API_KEY == "your_elevenlabs_api_key":
            raise Exception("ElevenLabs API key not configured")
        
        if not PEXELS_API_KEY or PEXELS_API_KEY == "your_pexels_api_key":
            raise Exception("Pexels API key not configured")
        
        # Generate voiceover
        tasks[task_id]['progress'] = 'Generating voiceover...'
        audio_file = await generate_voiceover(
            request.script_text, 
            task_id, 
            request.voice_id, 
            request.voice_settings
        )
        
        # Verify audio file exists
        if not os.path.exists(audio_file):
            raise Exception("Voiceover file was not created successfully")
        
        target_duration = get_audio_duration(audio_file)
        tasks[task_id]['duration'] = target_duration
        
        # Fetch videos
        required_clips = calculate_required_clips(target_duration)
        tasks[task_id]['progress'] = f'Fetching {required_clips} video clips...'
        video_data_list = await search_multiple_pexels_videos(request.search_query, required_clips)
        video_paths = await download_multiple_videos(video_data_list, task_id)
        
        # Convert to shorts
        tasks[task_id]['progress'] = 'Converting videos to shorts format...'
        converted_paths = await convert_multiple_to_shorts(video_paths, task_id)
        
        # Create compilation
        tasks[task_id]['progress'] = 'Creating video compilation...'
        compiled_video = await create_seamless_video_compilation(converted_paths, target_duration, task_id)
        
        # Merge audio
        tasks[task_id]['progress'] = 'Merging audio and video...'
        task_dir = TEMP_DIR / task_id
        merged_video = task_dir / "merged_video.mp4"
        merge_voiceover(compiled_video, audio_file, str(merged_video))
        
        # Generate captions
        tasks[task_id]['progress'] = 'Generating captions...'
        try:
            import whisper
            import whisper.audio
            import subprocess
            
            # Get the bundled ffmpeg path
            import imageio_ffmpeg as ffmpeg_lib
            ffmpeg_exe = ffmpeg_lib.get_ffmpeg_exe()
            
            # Store the original run function from whisper.audio module
            original_run = whisper.audio.run
            
            def patched_run(cmd, *args, **kwargs):
                # Replace 'ffmpeg' with full path in command
                if isinstance(cmd, list) and len(cmd) > 0 and cmd[0] == 'ffmpeg':
                    cmd = [ffmpeg_exe] + cmd[1:]
                elif isinstance(cmd, str) and cmd.startswith('ffmpeg'):
                    cmd = cmd.replace('ffmpeg', f'"{ffmpeg_exe}"', 1)
                
                print(f"Whisper running command: {cmd}")
                return original_run(cmd, *args, **kwargs)
            
            # Patch the run function in whisper.audio module
            whisper.audio.run = patched_run
            
            try:
                model = whisper.load_model("base")
                result = model.transcribe(audio_file, word_timestamps=True)
                words = extract_words_with_timestamps(result)
                print(f"Successfully transcribed with {len(words)} words")
            finally:
                # Restore original run function
                whisper.audio.run = original_run
                    
        except Exception as e:
            print(f"Whisper transcription failed: {e}")
            print(f"Audio file exists: {os.path.exists(audio_file)}")
            print(f"Audio file path: {audio_file}")
            print(f"FFmpeg exe: {ffmpeg_lib.get_ffmpeg_exe()}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            raise Exception(f"Caption generation failed: {str(e)}")
        
        if words:
            # Add captions
            tasks[task_id]['progress'] = 'Adding captions to video...'
            final_output = await add_captions_to_video(str(merged_video), words, request.font_size, task_id)
        else:
            # Use video without captions
            final_output = str(OUTPUT_DIR / f"{task_id}_final.mp4")
        
        # Update task status
        tasks[task_id]['status'] = 'completed'
        tasks[task_id]['progress'] = 'Video generation completed!'
        tasks[task_id]['output_file'] = final_output
        tasks[task_id]['completed_at'] = datetime.now()
        
        # If a callback URL was provided, attempt to deliver the video to it
        if request.callback_url:
            try:
                print(f"Posting completed video to callback URL: {request.callback_url}")
                with open(final_output, 'rb') as f:
                    files = {
                        'video': (f"generated_video_{task_id}.mp4", f, 'video/mp4')
                    }
                    data = {
                        'task_id': task_id,
                        'status': 'completed',
                        'duration': str(tasks[task_id].get('duration') or ''),
                        'message': 'Video generation completed',
                        'filename': f"generated_video_{task_id}.mp4",
                    }
                    # Best-effort delivery with timeout
                    requests.post(request.callback_url, data=data, files=files, timeout=30)
            except Exception as callback_error:
                print(f"Failed to POST video to callback URL: {callback_error}")
        
        # Cleanup temp files
        try:
            shutil.rmtree(TEMP_DIR / task_id, ignore_errors=True)
        except:
            pass
        
    except Exception as e:
        error_msg = f"Error in task {task_id}: {str(e)}"
        print(error_msg)
        tasks[task_id]['status'] = 'failed'
        tasks[task_id]['error'] = str(e)
        tasks[task_id]['completed_at'] = datetime.now()
        
        # Cleanup on error
        try:
            shutil.rmtree(TEMP_DIR / task_id, ignore_errors=True)
        except:
            pass

# === API ENDPOINTS ===

def start_generation_in_thread(request: VideoGenerationRequest, task_id: str):
    """Helper: run the async process_video_generation in a separate daemon thread."""
    def _target():
        try:
            # Each thread gets its own event loop via asyncio.run()
            asyncio.run(process_video_generation(request, task_id))
        except Exception as e:
            print(f"[background thread] Unhandled exception for task {task_id}: {e}")
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t

@app.post("/generate-video", response_model=VideoGenerationResponse)
async def generate_video(request: VideoGenerationRequest, background_tasks: BackgroundTasks):
    """Start video generation process"""
    task_id = str(uuid.uuid4())
    
    # Initialize task
    tasks[task_id] = {
        'status': 'pending',
        'progress': 'Task created',
        'error': None,
        'output_file': None,
        'duration': None,
        'created_at': datetime.now(),
        'completed_at': None
    }
    
    # Start background processing in a dedicated thread (avoids blocking the main event loop)
    # NOTE: we intentionally start our own thread (daemon=True) so the main FastAPI process remains responsive.
    start_generation_in_thread(request, task_id)
    
    return VideoGenerationResponse(
        task_id=task_id,
        status="pending",
        message="Video generation started. Use the task_id to check progress.",
    )

@app.get("/test-apis")
async def test_apis():
    """Test API connectivity"""
    results = {}
    
    # Test ElevenLabs
    try:
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {"xi-api-key": ELEVENLABS_API_KEY}
        response = requests.get(url, headers=headers, timeout=10)
        results["elevenlabs"] = {
            "status": "success" if response.status_code == 200 else "failed",
            "status_code": response.status_code,
            "message": "Connected successfully" if response.status_code == 200 else response.text[:200]
        }
    except Exception as e:
        results["elevenlabs"] = {"status": "error", "message": str(e)}
    
    # Test Pexels
    try:
        url = "https://api.pexels.com/videos/search?query=test&per_page=1"
        headers = {"Authorization": PEXELS_API_KEY}
        response = requests.get(url, headers=headers, timeout=10)
        results["pexels"] = {
            "status": "success" if response.status_code == 200 else "failed",
            "status_code": response.status_code,
            "message": "Connected successfully" if response.status_code == 200 else response.text[:200]
        }
    except Exception as e:
        results["pexels"] = {"status": "error", "message": str(e)}
    
    return results

@app.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """Get task status and progress"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    return TaskStatusResponse(
        task_id=task_id,
        status=task['status'],
        progress=task.get('progress'),
        error=task.get('error'),
        output_file=task.get('output_file'),
        duration=task.get('duration'),
        created_at=task['created_at'],
        completed_at=task.get('completed_at'),
        logs=task.get('logs', [])
    )

@app.get("/download/{task_id}")
async def download_video(task_id: str):
    """Download the generated video"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    if task['status'] != 'completed' or not task['output_file']:
        raise HTTPException(status_code=400, detail="Video not ready for download")
    
    output_file = Path(task['output_file'])
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    
    return FileResponse(
        path=str(output_file),
        filename=f"generated_video_{task_id}.mp4",
        media_type="video/mp4"
    )

@app.get("/tasks")
async def list_tasks():
    """List all tasks"""
    return {
        "tasks": [
            {
                "task_id": task_id,
                "status": task_data['status'],
                "created_at": task_data['created_at'],
                "duration": task_data['duration']
            }
            for task_id, task_data in tasks.items()
        ]
    }

@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """Delete a task and its associated files"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    # Delete output file if exists
    if task['output_file'] and Path(task['output_file']).exists():
        os.remove(task['output_file'])
    
    # Delete temp directory if exists
    temp_dir = TEMP_DIR / task_id
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    # Remove from tasks
    del tasks[task_id]
    
    return {"message": f"Task {task_id} deleted successfully"}

@app.get("/")
async def root():
    """API health check"""
    return {
        "message": "AI Video Generator API",
        "status": "running",
        "version": "1.0.0",
        "endpoints": {
            "POST /generate-video": "Start video generation",
            "GET /task/{task_id}": "Check task status", 
            "GET /download/{task_id}": "Download completed video",
            "GET /tasks": "List all tasks",
            "DELETE /task/{task_id}": "Delete task and files",
            "GET /test-apis": "Test API connectivity"
        }
    }

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # Disable reload in production
        log_level="info"
    )
