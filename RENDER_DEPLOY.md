# 🚀 Deploy to Render.com

## Memory-Optimized Video Generator for 2-4GB Instances

---

## Prerequisites

1. **GitHub Account** - Your code must be in a GitHub repo
2. **Render.com Account** - Sign up at https://render.com (free)
3. **API Keys**:
   - Pexels API Key: https://www.pexels.com/api/
   - ElevenLabs API Key: https://elevenlabs.io/

---

## Step 1: Prepare Your Repository

Make sure these files are committed to your GitHub repo:

```bash
git add .
git commit -m "Optimized for 2-4GB Render deployment with captions"
git push origin main
```

### Required Files (Already in your repo ✅)
- `main.py` - Optimized API server
- `requirements.txt` - Minimal dependencies
- `render.yaml` - Render configuration
- `.env.example` - Example environment variables
- `README.md` (optional)

---

## Step 2: Create New Web Service on Render

1. Go to https://dashboard.render.com/
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repository
4. Render will auto-detect the `render.yaml` file

---

## Step 3: Configure Environment Variables

In the Render dashboard, add these environment variables:

| Key | Value | Required |
|-----|-------|----------|
| `PEXELS_API_KEY` | Your Pexels API key | ✅ Yes |
| `ELEVENLABS_API_KEY` | Your ElevenLabs API key | ✅ Yes |
| `VOICE_ID` | Your custom voice ID | ❌ Optional |
| `PORT` | 8000 | ❌ Auto-set by Render |

### How to Add Environment Variables:
1. Go to your service dashboard
2. Click **"Environment"** tab
3. Click **"Add Environment Variable"**
4. Add each key-value pair
5. Click **"Save Changes"**

---

## Step 4: Choose Your Plan

### Option 1: Free Tier (Recommended for Testing)
- **Plan**: Starter (Free)
- **RAM**: 512MB
- **Limitations**: 
  - Spins down after 15 min inactivity
  - 750 hours/month free
  - Good for testing, but may be tight on RAM

### Option 2: Paid Plan (Recommended for Production)
- **Plan**: Standard
- **RAM**: 2GB
- **Cost**: $7/month
- **Benefits**:
  - Always on
  - No spin down
  - Perfect for our optimized code

### For 4GB+ (Heavy Usage)
- **Plan**: Pro
- **RAM**: 4GB+
- **Cost**: $25/month+

**Recommendation**: Start with **Standard (2GB)** for production use.

---

## Step 5: Deploy

1. Click **"Create Web Service"**
2. Render will automatically:
   - Install dependencies from `requirements.txt`
   - Start the server with `uvicorn main:app`
   - Provision disk storage (10GB for videos)

3. Wait for deployment (usually 2-5 minutes)

---

## Step 6: Test Your Deployment

Once deployed, Render will give you a URL like:
```
https://shorts-generator-optimized.onrender.com
```

### Test the API:

```bash
# Health check
curl https://your-app.onrender.com/

# Generate video
curl -X POST https://your-app.onrender.com/generate-video \
  -H "Content-Type: application/json" \
  -d '{
    "script_text": "Welcome to my channel. This is a test video with captions!",
    "search_query": "nature"
  }'

# Check status (use task_id from above)
curl https://your-app.onrender.com/task/{task_id}

# Download video (when completed)
curl -o video.mp4 https://your-app.onrender.com/download/{task_id}
```

---

## Configuration Details

### Memory Optimization Settings (Already Applied ✅)

```python
# In main.py
VIDEO_WIDTH = 720       # Lower resolution
VIDEO_HEIGHT = 1280
MAX_CLIPS = 5          # Limit clips
MAX_CONCURRENT_TASKS = 2  # Prevent memory spikes
ADD_CAPTIONS = True    # Lightweight FFmpeg captions
```

### What's Optimized:

1. ✅ **No Whisper** - Saved 1-2GB RAM
2. ✅ **No MoviePy** - Saved 300-500MB RAM
3. ✅ **No PIL/NumPy** - Saved 200-300MB RAM
4. ✅ **Pure FFmpeg** - Minimal memory footprint
5. ✅ **Aggressive cleanup** - Files deleted immediately
6. ✅ **Concurrent limiting** - Max 2 tasks at once
7. ✅ **Lower resolution** - 720x1280 vs 1080x1920

---

## Monitoring

### View Logs

1. Go to your service dashboard
2. Click **"Logs"** tab
3. Watch real-time logs for:
   - Video generation progress
   - Memory usage
   - Errors

### Check Memory Usage

Look for these log patterns:
```
[task_id] Starting video generation...
[task_id] Generating voiceover...
[task_id] Converting to vertical format...
[task_id] Adding captions...
[task_id] ✅ Completed!
```

### Memory Metrics in Render:
- Go to **"Metrics"** tab
- Watch **Memory** graph
- Should stay under 1.5GB peak

---

## Troubleshooting

### Service Fails to Start

**Check:**
1. Environment variables are set correctly
2. `requirements.txt` has all dependencies
3. Python version is 3.8+

**Fix:**
```bash
# View logs in Render dashboard
# Common issues:
- Missing API keys
- Wrong Python version
- Dependency installation failed
```

### Out of Memory (OOM)

**Symptoms:**
- Service restarts unexpectedly
- "Out of memory" in logs
- Slow responses

**Solutions:**
1. Upgrade to 2GB plan (Standard)
2. Reduce `MAX_CONCURRENT_TASKS` to 1:
   ```python
   MAX_CONCURRENT_TASKS = 1
   ```
3. Reduce `MAX_CLIPS` to 3:
   ```python
   MAX_CLIPS = 3
   ```

### Captions Not Working

**Check:**
1. `ADD_CAPTIONS = True` in `main.py`
2. Logs for FFmpeg errors
3. SRT file is being created

**The captions work on Linux (Render) but may fail on Windows!**

### Videos Taking Too Long

**Optimize:**
1. Use smaller search queries
2. Shorter script text
3. Check Pexels API response time
4. Check ElevenLabs API response time

---

## Cost Estimate

### Free Tier (Starter)
- **Cost**: $0/month
- **RAM**: 512MB
- **Usage**: 750 hours/month
- **Best for**: Testing only

### Recommended (Standard)
- **Cost**: $7/month
- **RAM**: 2GB
- **Usage**: Unlimited
- **Best for**: Light-medium production use
- **Videos**: ~100-200/day

### Heavy Usage (Pro)
- **Cost**: $25/month
- **RAM**: 4GB
- **Usage**: Unlimited
- **Best for**: Heavy production use
- **Videos**: 500+/day

### Disk Storage
- **Included**: 1GB free
- **Current**: 10GB configured in `render.yaml`
- **Cost**: ~$0.25/GB/month
- **Total**: ~$2.50/month for 10GB

**Estimated Total**: $7-10/month for production use

---

## Updating Your Deployment

### Automatic Deployment (Recommended)

Already configured in `render.yaml`:
```yaml
autoDeploy: true
```

Every `git push` to main branch will auto-deploy!

```bash
# Make changes
git add .
git commit -m "Updated feature"
git push origin main

# Render auto-deploys in 2-5 minutes
```

### Manual Deployment

1. Go to service dashboard
2. Click **"Manual Deploy"** 
3. Select branch
4. Click **"Deploy"**

---

## Production Checklist

- [ ] API keys added to Render environment variables
- [ ] Plan upgraded to Standard (2GB) or higher
- [ ] Disk storage configured (10GB)
- [ ] Health check working (`/`)
- [ ] Test video generation works
- [ ] Captions working properly
- [ ] Logs show no errors
- [ ] Memory usage under 1.5GB peak
- [ ] Response times acceptable
- [ ] Auto-deploy enabled
- [ ] Custom domain configured (optional)

---

## Optional Enhancements

### Add Custom Domain

1. Go to **"Settings"** tab
2. Click **"Add Custom Domain"**
3. Follow DNS setup instructions
4. SSL certificate auto-provisioned

### Add Health Monitoring

Use Render's built-in health checks:
```yaml
healthCheckPath: /
```

Or use external monitoring:
- UptimeRobot: https://uptimerobot.com/
- Pingdom: https://www.pingdom.com/
- StatusCake: https://www.statuscake.com/

### Set Up Alerts

1. Go to **"Settings"** → **"Notifications"**
2. Add email/Slack for:
   - Deployment failures
   - Service restarts
   - High memory usage

---

## Performance Tips

1. **Cache responses** if generating same videos
2. **Add Redis** for task queue (if scaling up)
3. **Use CDN** for video delivery (CloudFlare)
4. **Compress videos** further if needed
5. **Batch similar requests** to same time

---

## Getting Help

### Render Support
- Docs: https://render.com/docs
- Community: https://community.render.com/
- Support: support@render.com

### Your App Logs
```bash
# View in Render dashboard → Logs tab
# Or use Render CLI:
render logs -f
```

---

## Success! 🎉

Your memory-optimized video generator is now live on Render with:

✅ Captions enabled (works on Linux)
✅ 2-4GB RAM usage (perfect for 2GB plan)
✅ Fast video generation
✅ Auto-scaling ready
✅ Production-ready

**Your API is live at:**
`https://shorts-generator-optimized.onrender.com`

Test it and enjoy! 🚀

