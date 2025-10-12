# Quick Deployment Guide

## For 2-4GB Cloud Instances

### Prerequisites
- Python 3.8+
- FFmpeg will be auto-installed via `imageio-ffmpeg`
- API Keys: Pexels + ElevenLabs

---

## Option 1: Render.com (Recommended - Free 2GB)

1. Create a new **Web Service** on Render
2. Connect your GitHub repo
3. Configure:
   ```
   Build Command: pip install -r requirements.txt
   Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
   ```
4. Add Environment Variables:
   - `PEXELS_API_KEY`
   - `ELEVENLABS_API_KEY`
   - `VOICE_ID` (optional)
5. Deploy!

---

## Option 2: Railway.app

1. Create new project from GitHub
2. Add environment variables (same as above)
3. Railway auto-detects Python and runs
4. Set custom start command (optional):
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
   ```

---

## Option 3: Fly.io

1. Install Fly CLI: `curl -L https://fly.io/install.sh | sh`
2. Create `fly.toml`:
```toml
app = "your-app-name"

[build]
  builder = "paketobuildpacks/builder:base"

[env]
  PORT = "8000"

[[services]]
  http_checks = []
  internal_port = 8000
  protocol = "tcp"

  [[services.ports]]
    handlers = ["http"]
    port = 80

  [[services.ports]]
    handlers = ["tls", "http"]
    port = 443

[services.concurrency]
  hard_limit = 2
  soft_limit = 1
```

3. Deploy: `fly deploy`
4. Set secrets: `fly secrets set PEXELS_API_KEY=xxx ELEVENLABS_API_KEY=xxx`

---

## Option 4: Docker (Any Platform)

Use the included `Dockerfile`:

```bash
docker build -t video-generator .
docker run -p 8000:8000 \
  -e PEXELS_API_KEY=your_key \
  -e ELEVENLABS_API_KEY=your_key \
  video-generator
```

Or use `docker-compose.yml`:

```bash
# Create .env file first with your keys
docker-compose up
```

---

## Option 5: VPS (DigitalOcean, AWS, etc.)

```bash
# SSH into your server
ssh user@your-server

# Install Python 3.8+
sudo apt update
sudo apt install python3 python3-pip python3-venv

# Clone your repo
git clone your-repo-url
cd ShortsGenerator

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
nano .env
# Add your API keys

# Run with screen/tmux for persistence
screen -S video-api
python main.py

# Or use systemd service (recommended)
sudo nano /etc/systemd/system/video-api.service
```

Systemd service file:
```ini
[Unit]
Description=Video Generator API
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/ShortsGenerator
Environment="PATH=/path/to/.venv/bin"
ExecStart=/path/to/.venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable video-api
sudo systemctl start video-api
sudo systemctl status video-api
```

---

## Memory Monitoring

Monitor memory usage in production:

```bash
# Linux
watch -n 1 'free -h && ps aux | grep python'

# Or install htop
htop
```

If you see memory issues:
1. Reduce `MAX_CONCURRENT_TASKS` from 2 to 1
2. Reduce `MAX_CLIPS` from 5 to 3
3. Lower video resolution further in code

---

## Testing After Deployment

```bash
# Health check
curl https://your-app.com/

# Generate video
curl -X POST https://your-app.com/generate-video \
  -H "Content-Type: application/json" \
  -d '{
    "script_text": "This is a test video",
    "search_query": "nature"
  }'

# Check status (use task_id from above)
curl https://your-app.com/task/{task_id}

# Download (when completed)
curl -o video.mp4 https://your-app.com/download/{task_id}
```

---

## Troubleshooting

### "Server busy" error
- Too many concurrent requests
- Wait for existing tasks to complete
- Or increase `MAX_CONCURRENT_TASKS` (if you have RAM)

### Out of Memory
- Reduce concurrent tasks to 1
- Reduce max clips to 2-3
- Check for orphaned temp files in `temp_videos/`

### Slow processing
- Normal! Video processing is CPU-intensive
- Consider upgrading to 2 vCPUs
- FFmpeg encoding with `ultrafast` is already optimized

### Missing videos
- Check Pexels API quota
- Try different search queries
- Verify API key is correct

---

## Cost Estimates

| Platform | RAM | Cost/Month | Recommended For |
|----------|-----|------------|-----------------|
| Render.com | 2GB | Free | Testing |
| Railway.app | 2GB | $5-10 | Small projects |
| Fly.io | 1GB | $3-5 | Budget |
| DigitalOcean | 2GB | $12 | Production |
| AWS EC2 t3.small | 2GB | $15-20 | Enterprise |

---

## Production Checklist

- [ ] Environment variables set
- [ ] `.env` file NOT committed to git
- [ ] HTTPS enabled
- [ ] Monitoring set up
- [ ] Backup strategy for output videos
- [ ] Rate limiting (if public API)
- [ ] Authentication (if needed)
- [ ] CORS configured properly
- [ ] Error alerting (Sentry, etc.)
- [ ] Log rotation configured

Good luck! 🚀

