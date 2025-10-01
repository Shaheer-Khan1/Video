# main.py
import os
import requests
import json
import subprocess
import whisper
import asyncio
import uuid
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip, concatenate_videoclips
import imageio_ffmpeg as ffmpeg
import numpy as np
import threading

from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
from dotenv import load_dotenv

load_dotenv()

# === CONFIGURATION ===
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = os.getenv("VOICE_ID", "KUJ0dDUYhYz8c1Is7Ct6")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny")  # tiny|base
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "1"))  # serialize by default

if not PEXELS_API_KEY or not ELEVENLABS_API_KEY:
    raise ValueError("PEXELS_API_KEY and ELEVENLABS_API_KEY are required")

MIN_CLIPS = 3
MAX_CLIPS = 10

# Directories
TEMP_DIR = Path("temp_videos")
OUTPUT_DIR = Path("output_videos")
TEMP_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Semaphore to limit concurrency
TASK_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# === PYDANTIC MODELS ===
class VideoGenerationRequest(BaseModel):
    script_text: str = Field(..., min_length=10)
    search_query: str = Field(default="technology")
    font_size: int = Field(default=120, ge=60, le=200)
    voice_id: Optional[str] = None
    voice_settings: Optional[Dict[str, Any]] = None
    callback_url: Optional[str] = None

class VideoGenerationResponse(BaseModel):
    task_id: str
    status: str
    message: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: Optional[str] = None
    error: Optional[str] = None
    output_file: Optional[str] = None
    duration: Optional[float] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    logs: Optional[List[str]] = None

# === GLOBAL TASK STORAGE ===
tasks: Dict[str, Dict[str, Any]] = {}

def log_task(task_id: str, message: str):
    """Append a timestamped log and cap logs at 50 entries"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    if task_id in tasks:
        tasks[task_id].setdefault("logs", []).append(line)
        if len(tasks[task_id]["logs"]) > 50:
            tasks[task_id]["logs"] = tasks[task_id]["logs"][-50:]
        tasks[task_id]["progress"] = message
    print(line)

# === FASTAPI APP ===
app = FastAPI(title="AI Video Generator API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Ensure bundled ffmpeg is discoverable
try:
    _ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
    _ffmpeg_dir = os.path.dirname(_ffmpeg_exe)
    os.environ["IMAGEIO_FFMPEG_EXE"] = _ffmpeg_exe
    os.environ["PATH"] = f"{_ffmpeg_dir}{os.pathsep}{os.environ.get('PATH','')}"
except Exception as e:
    print(f"Warning: ffmpeg setup failed: {e}")

# === UTILITY FUNCTIONS ===
async def search_multiple_pexels_videos(query: str, num_clips: int = 5):
    video_urls = []
    page = 1
    per_page = min(num_clips, 15)
    while len(video_urls) < num_clips and page <= 3:
        url = f"https://api.pexels.com/videos/search?query={query}&per_page={per_page}&page={page}"
        headers = {"Authorization": PEXELS_API_KEY}
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise Exception(f"Pexels API error: {e}")
        for video in data.get("videos", []):
            if len(video_urls) >= num_clips: break
            video_files = sorted(video['video_files'], key=lambda x: x.get('width',0), reverse=True)
            if video_files:
                video_urls.append({"url": video_files[0]['link'], "duration": video.get('duration',10), "id": video['id']})
        page += 1
    if not video_urls:
        raise Exception("No videos found for query")
    return video_urls

async def download_multiple_videos(video_data_list, task_id: str):
    video_paths = []
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(exist_ok=True)
    for i, video_data in enumerate(video_data_list):
        video_path = task_dir / f"video_{i+1}.mp4"
        try:
            with requests.get(video_data['url'], stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(video_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            video_paths.append({"path": str(video_path), "duration": video_data['duration'], "id": video_data['id']})
            log_task(task_id, f"Downloaded video {i+1}/{len(video_data_list)}")
        except Exception as e:
            log_task(task_id, f"Download failed for video {i+1}: {e}")
    if not video_paths: raise Exception("No videos downloaded")
    return video_paths

def convert_to_shorts(input_path: str, output_path: str):
    ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
    command = [
        ffmpeg_exe, "-y", "-i", input_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v", "libx264", "-preset", "slow", "-crf", "22",
        "-c:a", "aac", output_path
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=120)

async def convert_multiple_to_shorts(video_paths, task_id: str):
    converted = []
    task_dir = TEMP_DIR / task_id
    for i, video in enumerate(video_paths):
        output_path = task_dir / f"shorts_{i+1}.mp4"
        try:
            convert_to_shorts(video['path'], str(output_path))
            converted.append({"path": str(output_path), "duration": video['duration'], "id": video['id']})
            log_task(task_id, f"Converted video {i+1}/{len(video_paths)} to shorts")
        except Exception as e:
            log_task(task_id, f"Conversion failed for video {i+1}: {e}")
    if not converted: raise Exception("No videos converted")
    return converted

async def generate_voiceover(script_text: str, task_id: str, voice_id: str = None, voice_settings: dict = None):
    task_dir = TEMP_DIR / task_id
    task_dir.mkdir(exist_ok=True)
    output_file = task_dir / "voiceover.mp3"
    current_voice = voice_id or VOICE_ID
    settings = {"stability":0.75,"similarity_boost":0.75,"style":0.2,"use_speaker_boost":True}
    if voice_settings: settings.update(voice_settings)
    ssml_text = f"<speak><prosody rate='90%' pitch='+2st'>{script_text.strip()}</prosody></speak>"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{current_voice}/stream"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type":"application/json", "Accept":"audio/mpeg"}
    payload = {"text": ssml_text,"model_id":"eleven_monolingual_v1","voice_settings":settings,"text_type":"ssml"}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        if response.status_code == 200:
            with open(output_file,"wb") as f: f.write(response.content)
            if not output_file.exists() or output_file.stat().st_size==0:
                raise Exception("Voiceover file empty")
            log_task(task_id,"Voiceover generated")
            return str(output_file)
        else:
            raise Exception(f"ElevenLabs error: {response.status_code} {response.text}")
    except Exception as e:
        log_task(task_id,f"Voiceover failed: {e}")
        raise

def get_audio_duration(audio_path: str) -> float:
    ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe,"-i",audio_path,"-f","null","-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    import re
    m = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})', result.stderr)
    if not m: raise Exception("Failed to parse audio duration")
    h,mn,s,cs = map(int,m.groups())
    return h*3600 + mn*60 + s + cs/100

def extract_words_with_timestamps(whisper_result):
    words=[]
    for seg in whisper_result["segments"]:
        if "words" in seg:
            for w in seg["words"]: words.append({"word":w["word"].strip(),"start":w["start"],"end":w["end"]})
    return words

def create_smart_word_groups(words):
    groups=[]
    group=[]
    for w in words:
        group.append(w)
        if any(p in w["word"] for p in ['.',',','!','?',';',':']) or len(group)>=3:
            groups.append(group.copy())
            group=[]
    if group: groups.append(group)
    return groups

def create_caption_clips(words, video_size, font_size=120, max_clips=50):
    clips=[]
    w,h=video_size
    font=None
    for fp in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","/System/Library/Fonts/Arial.ttf","arial.ttf"]:
        try: font=ImageFont.truetype(fp,font_size); break
        except: continue
    if not font: font=ImageFont.load_default()
    groups=create_smart_word_groups(words)
    total_clips=0
    for g in groups:
        if total_clips>=max_clips: break
        for idx,w in enumerate(g):
            if total_clips>=max_clips: break
            img=Image.new("RGBA",(w,h),(0,0,0,0))
            draw=ImageDraw.Draw(img)
            words_text=[x["word"] for x in g]
            full_text=" ".join(words_text)
            total_width=draw.textlength(full_text,font=font)
            start_x=max(20,(w-total_width)//2)
            current_x=start_x
            y_position=h-int(h*0.3)-font_size//2
            for i,word_text in enumerate(words_text):
                color="#FFD700" if i==idx else "#FFFFFF"
                stroke_width=2
                for dx in range(-stroke_width,stroke_width+1):
                    for dy in range(-stroke_width,stroke_width+1):
                        if dx!=0 or dy!=0: draw.text((current_x+dx,y_position+dy),word_text,font=font,fill="#000000")
                draw.text((current_x,y_position),word_text,font=font,fill=color)
                current_x+=draw.textlength(word_text,font=font)+draw.textlength(" ",font=font)
            clip=ImageClip(np.array(img),duration=w["end"]-w["start"]).set_start(w["start"]).set_position("center")
            clips.append(clip)
            total_clips+=1
    return clips

async def create_seamless_video_compilation(video_paths,target_duration,task_id):
    task_dir=TEMP_DIR/task_id
    output_path=task_dir/"compiled_video.mp4"
    clips=[]
    cur_duration=0
    idx=0
    loaded_videos=[]
    for v in video_paths:
        try: clip=VideoFileClip(v['path'],audio=False)
        except: continue
        if clip.duration>0: loaded_videos.append(clip)
    if not loaded_videos: raise Exception("No videos loaded")
    while cur_duration<target_duration:
        remaining=target_duration-cur_duration
        clip=loaded_videos[idx%len(loaded_videos)]
        if clip.duration<=remaining: sub=clip.subclip(0,clip.duration); dur=clip.duration
        else: sub=clip.subclip(0,remaining); dur=remaining
        clips.append(sub)
        cur_duration+=dur
        idx+=1
        log_task(task_id,f"Compiling video {cur_duration:.1f}/{target_duration:.1f}s")
    try:
        final=concatenate_videoclips(clips,method="compose")
        final.write_videofile(str(output_path),codec="libx264",audio_codec="aac",temp_audiofile=str(task_dir/"temp-audio.m4a"),remove_temp=True,verbose=False,logger=None)
        for c in clips: c.close()
        for c in loaded_videos: c.close()
        final.close()
        return str(output_path)
    except Exception as e:
        for c in clips: c.close()
        for c in loaded_videos: c.close()
        raise Exception(f"Failed video compilation: {e}")

async def transcribe_audio_whisper(audio_path: str, task_id: str):
    try:
        model = whisper.load_model(WHISPER_MODEL)
        log_task(task_id,"Starting transcription with Whisper")
        result = model.transcribe(audio_path, fp16=False)
        log_task(task_id,"Transcription completed")
        return extract_words_with_timestamps(result)
    except Exception as e:
        log_task(task_id,f"Transcription failed: {e}")
        return []

# === MAIN TASK FUNCTION ===
async def process_video_generation(request_data: VideoGenerationRequest, task_id: str):
    tasks[task_id] = {"status":"running","progress":"Starting task","created_at":datetime.now(),"logs":[]}
    try:
        async with TASK_SEMAPHORE:
            log_task(task_id,"Searching Pexels videos")
            video_list = await search_multiple_pexels_videos(request_data.search_query, num_clips=5)
            video_paths = await download_multiple_videos(video_list, task_id)
            shorts = await convert_multiple_to_shorts(video_paths, task_id)
            voice_file = await generate_voiceover(request_data.script_text, task_id, request_data.voice_id, request_data.voice_settings)
            audio_duration=get_audio_duration(voice_file)
            final_video_path = await create_seamless_video_compilation(shorts,audio_duration,task_id)
            words = await transcribe_audio_whisper(voice_file, task_id)
            # Generate captions and overlay
            if words:
                video_clip = VideoFileClip(final_video_path)
                captions = create_caption_clips(words, video_clip.size, font_size=request_data.font_size)
                composite = CompositeVideoClip([video_clip]+captions)
                output_file = OUTPUT_DIR / f"{task_id}_final.mp4"
                composite.write_videofile(str(output_file),codec="libx264",audio_codec="aac",temp_audiofile=str(TEMP_DIR/task_id/"temp_audio_final.m4a"),remove_temp=True,verbose=False,logger=None)
                video_clip.close(); composite.close()
                tasks[task_id].update({"status":"completed","completed_at":datetime.now(),"output_file":str(output_file),"duration":audio_duration})
                log_task(task_id,"Task completed successfully")
            else:
                tasks[task_id].update({"status":"failed","error":"Transcription failed"})
    except Exception as e:
        tasks[task_id].update({"status":"failed","error":str(e),"completed_at":datetime.now()})
        log_task(task_id,f"Task failed: {e}")
    finally:
        # Cleanup temp files
        shutil.rmtree(TEMP_DIR/task_id, ignore_errors=True)

# === API ENDPOINTS ===
@app.post("/generate_video", response_model=VideoGenerationResponse)
async def generate_video_endpoint(request_data: VideoGenerationRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    background_tasks.add_task(process_video_generation, request_data, task_id)
    return VideoGenerationResponse(task_id=task_id,status="running",message="Task started")

@app.get("/task_status/{task_id}", response_model=TaskStatusResponse)
async def task_status(task_id: str):
    task = tasks.get(task_id)
    if not task: raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(task_id=task_id, **task)

@app.get("/download/{task_id}")
async def download_video(task_id: str):
    task = tasks.get(task_id)
    if not task or task.get("status")!="completed": raise HTTPException(status_code=404, detail="Video not ready")
    return FileResponse(task["output_file"], filename=os.path.basename(task["output_file"]))

# === RUN SERVER ===
if __name__=="__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
