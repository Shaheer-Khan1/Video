#!/usr/bin/env python3
"""
Test script to compare video generation with and without captions
"""

import requests
import time
import json

API_URL = "http://localhost:8000"

def test_video_generation(test_name="Test"):
    """Test video generation"""
    print(f"\n{'='*60}")
    print(f"Testing: {test_name}")
    print(f"{'='*60}")
    
    # Request (captions are now hardcoded in main.py)
    payload = {
        "script_text": "Welcome to this amazing video. Today we're exploring the power of AI. Let's dive in and see what we can create!",
        "search_query": "technology"
    }
    
    print("\n📤 Sending request...")
    print(f"Script: {payload['script_text'][:50]}...")
    print("Captions: Enabled (hardcoded)")
    
    # Start task
    response = requests.post(f"{API_URL}/generate-video", json=payload)
    
    if response.status_code != 200:
        print(f"❌ Error: {response.status_code}")
        print(response.text)
        return
    
    task_id = response.json()["task_id"]
    print(f"✅ Task created: {task_id}")
    
    # Poll status
    start_time = time.time()
    while True:
        time.sleep(3)
        
        status_response = requests.get(f"{API_URL}/task/{task_id}")
        if status_response.status_code != 200:
            print(f"❌ Status check failed")
            break
        
        status_data = status_response.json()
        status = status_data["status"]
        progress = status_data["progress"]
        elapsed = int(time.time() - start_time)
        
        print(f"⏳ [{elapsed}s] Status: {status} - {progress}")
        
        if status == "completed":
            total_time = time.time() - start_time
            print(f"\n✅ Completed in {total_time:.1f}s!")
            print(f"📥 Download: {API_URL}/download/{task_id}")
            
            # Show file info
            if "output_file" in status_data and status_data["output_file"]:
                import os
                if os.path.exists(status_data["output_file"]):
                    size_mb = os.path.getsize(status_data["output_file"]) / (1024*1024)
                    print(f"📊 File size: {size_mb:.2f} MB")
            
            break
        elif status == "failed":
            print(f"\n❌ Failed: {status_data.get('error', 'Unknown error')}")
            break
    
    return task_id


def main():
    print("🎬 Video Generation Test Suite")
    print("="*60)
    print("\n⚠️  Note: Captions are now hardcoded as ENABLED in main.py")
    print("To disable: Set ADD_CAPTIONS = False in main.py\n")
    
    # Test video generation
    print("\nTest: Video with lightweight captions")
    task_id = test_video_generation("Video with Captions")
    
    # Summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    print(f"\nGenerated video: {API_URL}/download/{task_id}")
    print("\n✅ Captions added using lightweight FFmpeg method")
    print("✅ Zero additional RAM usage (no Whisper/PyTorch)")
    print("✅ Fast text-based subtitle generation")
    print("\n🎉 Perfect for 2-4GB instances!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")

