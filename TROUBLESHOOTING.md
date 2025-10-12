# Troubleshooting Guide

## Common Errors and Fixes

### 1. FFmpeg Concat Error (Exit Status 4294967294)

**Error:**
```
Command '['ffmpeg', ..., 'concat', ...]' returned non-zero exit status 4294967294
```

**Causes:**
- File paths not properly formatted for Windows
- Missing or corrupted video files
- Incompatible video codecs

**Fix Applied:**
- ✅ Now using absolute paths with forward slashes
- ✅ Forcing re-encode instead of stream copy
- ✅ Better error logging to show actual FFmpeg errors

**If still occurring:**
```python
# Check if videos exist and are valid
import os
for path in paths:
    print(f"File exists: {os.path.exists(path)}")
    print(f"File size: {os.path.getsize(path)} bytes")
```

---

### 2. Voiceover File Not Created

**Error:**
```
[Errno 2] No such file or directory: 'temp_videos\\{task_id}\\voice.mp3'
```

**Causes:**
- ElevenLabs API key invalid or expired
- Network connectivity issues
- API quota exceeded

**Fix:**
```bash
# Test your API key
curl https://api.elevenlabs.io/v1/voices \
  -H "xi-api-key: YOUR_KEY_HERE"
```

**Check quota:**
- Free tier: 10,000 characters/month
- Log in to elevenlabs.io to check usage

---

### 3. Memory Issues / Server Crash

**Error:**
- Server stops responding
- Process killed
- Out of memory errors

**Fix:**
```python
# In main.py, reduce concurrent tasks
MAX_CONCURRENT_TASKS = 1  # Instead of 2

# Reduce max clips
MAX_CLIPS = 3  # Instead of 5

# Lower resolution further
VIDEO_WIDTH = 540  # Instead of 720
VIDEO_HEIGHT = 960  # Instead of 1280
```

---

### 4. Pexels API - No Videos Found

**Error:**
```
No videos found for query: {query}
```

**Causes:**
- Invalid API key
- Search query too specific
- API rate limit hit

**Fix:**
```python
# Try more generic queries
"nature"
"technology"
"business"
"people"
"abstract"

# Check API status
curl https://api.pexels.com/videos/search?query=test&per_page=1 \
  -H "Authorization: YOUR_KEY_HERE"
```

---

### 5. Video Download Failures

**Error:**
```
Download failed for clip X: ...
```

**Causes:**
- Network timeout
- Pexels server issues
- File too large

**Current Handling:**
- Already skips failed downloads
- Continues with available clips
- Minimum 2 clips required

**If all downloads fail:**
- Check internet connection
- Try different search query
- Check Pexels status page

---

### 6. FFmpeg Not Found (Windows)

**Error:**
```
FileNotFoundError: ffmpeg not found
```

**Fix:**
```bash
# The app uses bundled FFmpeg from imageio-ffmpeg
# If missing, reinstall:
pip uninstall imageio-ffmpeg
pip install imageio-ffmpeg==0.6.0

# Verify installation
python -c "import imageio_ffmpeg as ffmpeg; print(ffmpeg.get_ffmpeg_exe())"
```

---

### 7. Port Already in Use

**Error:**
```
OSError: [Errno 48] Address already in use
```

**Fix:**
```bash
# Kill existing process (Windows)
netstat -ano | findstr :8000
taskkill /PID <PID_NUMBER> /F

# Or use different port
uvicorn main:app --port 8001
```

---

### 8. Task Status Shows "Pending" Forever

**Causes:**
- Background task crashed
- Exception not caught
- Server restarted mid-task

**Fix:**
- Check server logs for errors
- Restart the server
- Delete stuck task temp folder:
  ```bash
  rm -rf temp_videos/{task_id}
  ```

---

### 9. Video Quality Too Low

**Current Settings:**
- Resolution: 720x1280
- CRF: 28 (lower quality)
- Preset: ultrafast (fast, lower quality)

**To Improve Quality:**
```python
# In main.py, change:
VIDEO_WIDTH = 1080  # Higher resolution
VIDEO_HEIGHT = 1920

# In convert_to_vertical() and other functions:
"-crf", "23",  # Better quality (18-23 is good, lower = better)
"-preset", "medium",  # Slower but better quality
```

**Trade-off:** Higher quality = more RAM + slower processing

---

### 10. Callback URL Not Working

**Causes:**
- Callback URL not reachable
- Timeout (30s limit)
- Server rejecting POST request

**Debug:**
```python
# Check if callback was attempted (server logs will show)
# Test your callback endpoint:
curl -X POST https://your-callback-url.com/webhook \
  -F "video=@test.mp4" \
  -F "task_id=test" \
  -F "status=completed"
```

---

## Debug Mode

Enable verbose FFmpeg output:

```python
# In any FFmpeg subprocess.run() call, remove capture_output=True
# and add stdout/stderr to see FFmpeg's output:

result = subprocess.run(
    cmd, 
    check=True, 
    # capture_output=True,  # Comment out
    text=True, 
    timeout=120
)
```

---

## Check System Resources

### Windows:
```powershell
# Check memory
Get-Process python | Select-Object -Property Name, CPU, WS

# Monitor in real-time
while($true) { 
    Clear-Host
    Get-Process python | Format-Table Name, CPU, @{Label="Memory(MB)"; Expression={[int]($_.WS / 1MB)}}
    Start-Sleep 2
}
```

### Linux:
```bash
# Monitor memory
watch -n 1 'ps aux | grep python'

# Or use htop
htop -p $(pgrep -f "python main.py")
```

---

## Reset Everything

If all else fails:

```bash
# Stop server
# Ctrl+C or kill process

# Clean up
rm -rf temp_videos/*
rm -rf output_videos/*
rm -rf __pycache__

# Reinstall dependencies
pip uninstall -r requirements.txt -y
pip install -r requirements.txt

# Restart server
python main.py
```

---

## Getting Help

If you're still stuck:

1. **Check logs** - Look for the full error message
2. **Test APIs** - Verify your API keys work
3. **Check FFmpeg** - Make sure it's working:
   ```bash
   python -c "import imageio_ffmpeg as ffmpeg; import subprocess; subprocess.run([ffmpeg.get_ffmpeg_exe(), '-version'])"
   ```
4. **Simplify** - Try with shortest script_text and simple search_query
5. **Monitor** - Watch memory and CPU usage during processing

---

## Performance Optimization Tips

1. **Reduce concurrent tasks to 1** if memory is tight
2. **Lower video resolution** to 540x960 for even less memory
3. **Use SSD storage** for temp files (faster I/O)
4. **Increase timeout** if videos are large: `timeout=300`
5. **Use CDN for output videos** instead of serving from API
6. **Add Redis** for task queue (if scaling up)
7. **Use nginx** as reverse proxy for better performance

---

## Current Optimizations Applied

✅ Pure FFmpeg processing (no Python video libraries)
✅ Absolute paths with forward slashes for Windows
✅ Stream-based downloads (no bulk loading)
✅ Immediate file cleanup after each step
✅ Aggressive garbage collection
✅ Concurrent task limiting
✅ Better error messages with FFmpeg stderr
✅ Fallback encoding when stream copy fails
✅ Timeout protection on all operations

Your code is now **production-ready**! 🚀

