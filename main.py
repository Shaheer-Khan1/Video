"""
AI Video Generator API (Memory-Optimized Edition)
==================================================
Optimized for 2-4GB instances:
- No Whisper (removed - uses ~1-2GB RAM)
- No MoviePy (replaced with FFmpeg subprocess)
- No PIL/ImageDraw (replaced with FFmpeg drawtext)
- Aggressive memory cleanup
- 720p resolution (not 1080p)
- Max 5 clips
- Stream processing only
"""

import os
import requests
import json
import subprocess
import uuid
import shutil
import gc
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import imageio_ffmpeg as ffmpeg

from dotenv import load_dotenv
load_dotenv()

# === CONFIGURATION ===
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("VOICE_ID", "KUJ0dDUYhYz8c1Is7Ct6")

# Validate required environment variables
if not PEXELS_API_KEY:
    raise ValueError("PEXELS_API_KEY environment variable is required")
if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY environment variable is required")

# Memory-optimized settings
MIN_CLIPS = 2
MAX_CLIPS = 5  # Reduced from 10
VIDEO_WIDTH = 720  # Reduced from 1080
VIDEO_HEIGHT = 1280  # Reduced from 1920
MAX_CONCURRENT_TASKS = 2  # Limit concurrent processing

# Create directories
TEMP_DIR = Path("temp_videos")
OUTPUT_DIR = Path("output_videos")
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# === PYDANTIC MODELS ===
class VideoGenerationRequest(BaseModel):
    script_text: str = Field(..., description="The script text for voiceover", min_length=10)
    search_query: str = Field(default="technology", description="Search query for relevant video clips")
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID (optional)")
    callback_url: Optional[str] = Field(default=None, description="If provided, the server will POST the generated video to this URL when done.")

# === CAPTION SETTINGS (Hardcoded) ===
# Captions enabled for Linux/Render deployment
# Note: May not work on Windows due to path escaping, but works perfectly on Linux
# Memory impact: ~0MB (just text processing, no ML models)
ADD_CAPTIONS = True  # Enabled for Render/Linux deployment
CAPTION_FONT_SIZE = 28  # Font size for captions (16-48 recommended)

class VideoGenerationResponse(BaseModel):
    task_id: str
    status: str
    message: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str  # "pending", "processing", "completed", "failed"
    progress: str
    error: Optional[str] = None
    output_file: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

# === GLOBAL TASK STORAGE ===
tasks: Dict[str, Dict[str, Any]] = {}
active_tasks = 0  # Track concurrent tasks

# === MEMORY MANAGEMENT ===
def free_memory() -> None:
    """Aggressive garbage collection"""
    gc.collect()
    gc.collect()  # Call twice for thorough cleanup
    gc.collect()

def log_task(task_id: str, message: str) -> None:
    """Log task progress"""
    print(f"[{task_id}] {message}")
    if task_id in tasks:
        tasks[task_id]['progress'] = message

# === FASTAPI APP ===
app = FastAPI(
    title="AI Video Generator API (Memory-Optimized)",
    description="Generate short-form videos optimized for 2-4GB instances",
    version="2.0.0-optimized"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === VIDEO PROCESSING FUNCTIONS ===
async def search_pexels_videos(query: str, num_clips: int):
    """Fetch video clips from Pexels API"""
    log_task("search", f"Searching for {num_clips} clips: '{query}'")
    url = f"https://api.pexels.com/videos/search?query={query}&per_page={min(num_clips, 15)}"
    headers = {"Authorization": PEXELS_API_KEY}
    
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        
        videos = []
        for v in data.get('videos', [])[:num_clips]:
            video_files = v.get('video_files', [])
            if video_files:
                videos.append(video_files[0]['link'])
        
        if not videos:
            raise Exception(f"No videos found for: {query}")
        
        return videos
    except Exception as e:
        raise Exception(f"Pexels API error: {e}")

async def download_videos(video_urls: List[str], task_id: str):
    """Download videos with streaming to minimize memory"""
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(exist_ok=True)
    paths = []
    
    for i, url in enumerate(video_urls):
        out_path = task_dir / f"clip_{i+1}.mp4"
        log_task(task_id, f"Downloading clip {i+1}/{len(video_urls)}")
        
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(out_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            paths.append(str(out_path))
        except Exception as e:
            print(f"Download failed for clip {i+1}: {e}")
            continue
    
    if not paths:
        raise Exception("Failed to download any videos")
    
    free_memory()
    return paths

def convert_to_vertical(input_path: str, output_path: str):
    """Convert video to vertical format using FFmpeg - memory efficient"""
    exe = ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe, "-y", "-i", input_path,
        "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        error_msg = f"FFmpeg convert failed: {e.stderr}"
        print(error_msg)
        raise Exception(error_msg)

async def convert_videos_to_vertical(paths: List[str], task_id: str):
    """Convert all videos to vertical format"""
    task_dir = TEMP_DIR / task_id
    converted = []
    
    for i, path in enumerate(paths):
        out_path = task_dir / f"vertical_{i+1}.mp4"
        log_task(task_id, f"Converting {i+1}/{len(paths)} to vertical")
        
        try:
            convert_to_vertical(path, str(out_path))
            converted.append(str(out_path))
            # Delete original to save space
            Path(path).unlink(missing_ok=True)
            free_memory()
        except Exception as e:
            print(f"Conversion failed for clip {i+1}: {e}")
            continue
    
    if not converted:
        raise Exception("Failed to convert any videos")
    
    return converted

async def generate_voiceover(script_text: str, task_id: str, voice_id: Optional[str]):
    """Generate voiceover using ElevenLabs API"""
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(exist_ok=True)
    output_file = task_dir / "voice.mp3"
    
    voice = voice_id or VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    payload = {
        "text": script_text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.7, "similarity_boost": 0.7}
    }
    
    try:
        log_task(task_id, "Generating voiceover...")
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        response.raise_for_status()
        
        with open(output_file, "wb") as f:
            f.write(response.content)
        
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise Exception("Voiceover file creation failed")
        
        log_task(task_id, "Voiceover generated")
        return str(output_file)
    except Exception as e:
        raise Exception(f"Voiceover generation failed: {e}")

def get_audio_duration(audio_path: str) -> float:
    """Get audio duration using FFmpeg"""
    exe = ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-i", audio_path]
    result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not match:
        return 10.0  # Default fallback
    
    h, m, s = map(float, match.groups())
    return h * 3600 + m * 60 + s

async def compile_videos(paths: List[str], target_duration: float, task_id: str):
    """Compile videos to match target duration using FFmpeg"""
    task_dir = TEMP_DIR / task_id
    output_path = task_dir / "compiled.mp4"
    list_file = task_dir / "list.txt"
    
    # Create concat list with absolute paths and proper escaping for Windows
    with open(list_file, "w", encoding="utf-8") as f:
        current_dur = 0.0
        idx = 0
        while current_dur < target_duration and paths:
            # Convert to absolute path and use forward slashes for FFmpeg
            abs_path = Path(paths[idx % len(paths)]).resolve()
            # FFmpeg on Windows needs forward slashes or escaped backslashes
            path_str = str(abs_path).replace('\\', '/')
            f.write(f"file '{path_str}'\n")
            current_dur += 5  # Approximate, FFmpeg will handle
            idx += 1
    
    exe = ffmpeg.get_ffmpeg_exe()
    
    # Try concat with re-encode (more reliable than copy)
    cmd = [
        exe, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path)
    ]
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        log_task(task_id, "Videos compiled")
    except subprocess.CalledProcessError as e:
        # Log the actual error for debugging
        error_msg = f"FFmpeg concat failed: {e.stderr}"
        print(error_msg)
        raise Exception(error_msg)
    
    free_memory()
    return str(output_path)

def merge_audio_video(video_path: str, audio_path: str, output_path: str):
    """Merge audio with video using FFmpeg"""
    exe = ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe, "-y", "-i", video_path, "-i", audio_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        error_msg = f"FFmpeg merge failed: {e.stderr}"
        print(error_msg)
        raise Exception(error_msg)

# === LIGHTWEIGHT CAPTIONING (No Whisper!) ===

def create_srt_from_text(text: str, duration: float, task_id: str) -> str:
    """Create SRT subtitle file by splitting text into chunks (no Whisper needed)"""
    task_dir = TEMP_DIR / task_id
    srt_path = task_dir / "captions.srt"
    
    # Split text into sentences or chunks
    import re
    # Split on sentence endings but keep punctuation
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if not sentences or sentences == ['']:
        # If no sentences, split into chunks of ~10 words
        words = text.split()
        chunk_size = 10
        sentences = [' '.join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
    
    # Calculate timing for each sentence
    time_per_sentence = duration / len(sentences)
    
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, sentence in enumerate(sentences):
            start_time = i * time_per_sentence
            end_time = (i + 1) * time_per_sentence
            
            # Format: HH:MM:SS,mmm
            start_str = format_srt_time(start_time)
            end_str = format_srt_time(end_time)
            
            f.write(f"{i + 1}\n")
            f.write(f"{start_str} --> {end_str}\n")
            f.write(f"{sentence.strip()}\n\n")
    
    return str(srt_path)

def format_srt_time(seconds: float) -> str:
    """Format seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def add_captions_with_ffmpeg(video_path: str, srt_path: str, output_path: str, font_size: int = 24):
    """Add captions to video using FFmpeg's subtitles filter (very lightweight)"""
    exe = ffmpeg.get_ffmpeg_exe()
    
    # For Windows, use absolute path with forward slashes and proper escaping
    # The subtitles filter needs special handling on Windows
    abs_srt_path = str(Path(srt_path).resolve())
    
    # On Windows, we need to escape the path properly for FFmpeg
    # Replace backslashes with forward slashes
    srt_path_ffmpeg = abs_srt_path.replace('\\', '/')
    # Escape the colon in drive letter (C: becomes C\\:)
    srt_path_ffmpeg = srt_path_ffmpeg.replace(':', '\\:')
    
    cmd = [
        exe, "-y", "-i", video_path,
        "-vf", (
            f"subtitles={srt_path_ffmpeg}:force_style='"
            f"FontName=Arial,FontSize={font_size},PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,BorderStyle=3,Outline=2,Shadow=1,"
            f"Alignment=2,MarginV=80'"
        ),
        "-c:a", "copy",  # Copy audio (no re-encode)
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as e:
        error_msg = f"FFmpeg caption failed: {e.stderr}"
        print(error_msg)
        raise Exception(error_msg)

async def process_video_generation(request: VideoGenerationRequest, task_id: str):
    """Main video processing pipeline - memory optimized"""
    global active_tasks
    
    try:
        active_tasks += 1
        tasks[task_id]['status'] = 'processing'
        log_task(task_id, "Starting video generation...")
        
        # Step 1: Generate voiceover
        audio_path = await generate_voiceover(request.script_text, task_id, request.voice_id)
        duration = get_audio_duration(audio_path)
        log_task(task_id, f"Target duration: {duration:.1f}s")
        
        # Step 2: Fetch and download videos
        num_clips = max(MIN_CLIPS, min(MAX_CLIPS, int(duration / 10) + 1))
        video_urls = await search_pexels_videos(request.search_query, num_clips)
        downloaded = await download_videos(video_urls, task_id)
        
        # Step 3: Convert to vertical format
        log_task(task_id, "Converting to vertical format...")
        converted = await convert_videos_to_vertical(downloaded, task_id)
        
        # Step 4: Compile videos
        log_task(task_id, "Compiling videos...")
        compiled = await compile_videos(converted, duration, task_id)
        
        # Step 5: Merge audio with video
        log_task(task_id, "Merging audio...")
        task_dir = TEMP_DIR / task_id
        merged_video = task_dir / "merged.mp4"
        merge_audio_video(compiled, audio_path, str(merged_video))
        
        # Step 6: Add captions (hardcoded - always enabled, lightweight!)
        if ADD_CAPTIONS:
            log_task(task_id, "Adding captions...")
            srt_path = create_srt_from_text(request.script_text, duration, task_id)
            final_output = OUTPUT_DIR / f"{task_id}_final.mp4"
            add_captions_with_ffmpeg(str(merged_video), srt_path, str(final_output), CAPTION_FONT_SIZE)
            log_task(task_id, "Captions added")
        else:
            # No captions - use merged video as final
            final_output = OUTPUT_DIR / f"{task_id}_final.mp4"
            shutil.move(str(merged_video), str(final_output))
        
        # Update task
        tasks[task_id]['status'] = 'completed'
        tasks[task_id]['output_file'] = str(final_output)
        tasks[task_id]['completed_at'] = datetime.now()
        log_task(task_id, "✅ Completed!")
        
        # Callback if provided
        if request.callback_url:
            try:
                with open(final_output, 'rb') as f:
                    requests.post(
                        request.callback_url,
                        files={'video': (f"{task_id}.mp4", f, "video/mp4")},
                        data={'task_id': task_id, 'status': 'completed'},
                        timeout=30
                    )
            except Exception as e:
                print(f"Callback failed: {e}")
        
        # Cleanup
        shutil.rmtree(TEMP_DIR / task_id, ignore_errors=True)
        free_memory()
        
    except Exception as e:
        tasks[task_id]['status'] = 'failed'
        tasks[task_id]['error'] = str(e)
        tasks[task_id]['completed_at'] = datetime.now()
        log_task(task_id, f"❌ Failed: {e}")
        shutil.rmtree(TEMP_DIR / task_id, ignore_errors=True)
        free_memory()
    finally:
        active_tasks -= 1

# === API ENDPOINTS ===

@app.post("/generate-video", response_model=VideoGenerationResponse)
async def generate_video(request: VideoGenerationRequest, background_tasks: BackgroundTasks):
    """Start video generation"""
    global active_tasks
    
    # Limit concurrent tasks to prevent memory overload
    if active_tasks >= MAX_CONCURRENT_TASKS:
        raise HTTPException(503, f"Server busy. Max {MAX_CONCURRENT_TASKS} concurrent tasks allowed.")
    
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        'status': 'pending',
        'progress': 'Task created',
        'error': None,
        'output_file': None,
        'created_at': datetime.now(),
        'completed_at': None
    }
    
    background_tasks.add_task(process_video_generation, request, task_id)
    
    return VideoGenerationResponse(
        task_id=task_id,
        status="pending",
        message="Video generation started"
    )

@app.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """Get task status"""
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    task = tasks[task_id]
    return TaskStatusResponse(
        task_id=task_id,
        status=task['status'],
        progress=task['progress'],
        error=task.get('error'),
        output_file=task.get('output_file'),
        created_at=task['created_at'],
        completed_at=task.get('completed_at')
    )

@app.get("/download/{task_id}")
async def download_video(task_id: str):
    """Download generated video"""
    if task_id not in tasks:
        raise HTTPException(404, "Task not found")
    
    task = tasks[task_id]
    if task['status'] != 'completed':
        raise HTTPException(400, "Video not ready")
    
    file_path = task.get('output_file')
    if not file_path or not Path(file_path).exists():
        raise HTTPException(404, "File not found")
    
    return FileResponse(file_path, media_type="video/mp4", filename=f"{task_id}.mp4")

@app.get("/")
def root():
    return {
        "status": "ok",
        "version": "2.0-optimized",
        "message": "AI Video Generator (Memory Optimized for 2-4GB)",
        "active_tasks": active_tasks,
        "max_concurrent": MAX_CONCURRENT_TASKS
    }

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, workers=1)