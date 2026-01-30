from flask import Blueprint, redirect, render_template, request, flash, send_file, url_for, session
from flask_login import login_required, current_user
from .models import Video
from . import db

import requests
import os
import re

views = Blueprint("views", __name__)

# Piped API instances (more reliable than Invidious)
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.in.projectsegfau.lt",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.reallyaweso.me",
    "https://api.piped.yt",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.syncpundit.io",
]

## Pages

@views.route("/")
def home():
    session.clear()
    return redirect(url_for("views.video"))

@views.route("/video", methods=["GET", "POST"])
def video():
    if request.method == "POST":
        url = request.form.get("url")
        date = request.form.get("date")
        
        session.clear()

        if not url or ("youtube.com" not in url and "youtu.be" not in url):
            flash("Please enter a valid YouTube URL.", category="error")
            return render_template("video.html", user=current_user)

        file_type = "mp4" if request.form["convert"] == "mp4" else "mp3"
        downloads_path = os.path.join(os.getcwd(), "temp")
        os.makedirs(downloads_path, exist_ok=True)

        try:
            result = download_with_piped(url, file_type, downloads_path)
            file_path = result['file_path']
            title = result['title']
        except Exception as e:
            print(f"Download error: {e}")
            flash(f"Video could not be downloaded. Error: {e}", category="error")
            return render_template("video.html", user=current_user)

        save_history(url, date, title, "video", file_type)
        
        session["download_file_path"] = file_path
        session["download_title"] = title
        session["download_file_type"] = file_type
        
        return redirect(url_for("views.download_page"))

    session["playlist_url"] = ""
    try: 
        url = session["video_url"]
    except: 
        url = ""

    return render_template("video.html", user=current_user, url=url)

@views.route("/download")
def download_page():
    file_path = session.get("download_file_path")
    title = session.get("download_title")
    file_type = session.get("download_file_type")
    
    if not file_path or not os.path.exists(file_path):
        flash("No file available for download.", category="error")
        return redirect(url_for("views.video"))
    
    return render_template("download.html", user=current_user, title=title, file_type=file_type)

@views.route("/download-file")
def download_file():
    file_path = session.get("download_file_path")
    
    if not file_path or not os.path.exists(file_path):
        flash("File not found.", category="error")
        return redirect(url_for("views.video"))
    
    try:
        return send_file(path_or_file=file_path, as_attachment=True)
    except Exception:
        flash("Could not send file for download.", category="error")
        return redirect(url_for("views.video"))

@views.route("/playlist", methods=["GET", "POST"])
def playlist():
    session["video_url"] = ""
    try: 
        url = session["playlist_url"]
    except: 
        url = ""
    return render_template("playlist.html", user=current_user, url=url)

@views.route("/history", methods=["GET", "POST"])
@login_required
def history():
    if request.method == "POST":
        if "convert" not in request.form:
            try:
                db.session.query(Video).delete()
                db.session.commit()
                flash("Cleared History", category="success")
            except:
                db.session.rollback()
                flash("Could not clear history.", category="error")
    
    session.clear()
    return render_template("history.html", user=current_user)

@views.route("/search", methods=["GET", "POST"])
def search():
    session.clear()
    return render_template("search.html", user=current_user)

## Helper Functions

def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL"""
    if 'youtu.be/' in url:
        return url.split('youtu.be/')[-1].split('?')[0].split('&')[0]
    elif 'v=' in url:
        return url.split('v=')[-1].split('&')[0].split('#')[0]
    elif '/embed/' in url:
        return url.split('/embed/')[-1].split('?')[0]
    elif '/shorts/' in url:
        return url.split('/shorts/')[-1].split('?')[0]
    return None

def download_with_piped(url: str, file_type: str, downloads_path: str) -> dict:
    """Download video using Piped API"""
    
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Could not extract video ID from URL")
    
    print(f"Video ID: {video_id}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    }
    
    last_error = None
    
    for instance in PIPED_INSTANCES:
        try:
            print(f"Trying: {instance}")
            
            # Get video streams
            api_url = f"{instance}/streams/{video_id}"
            response = requests.get(api_url, headers=headers, timeout=20)
            
            if response.status_code != 200:
                print(f"Status {response.status_code}")
                continue
            
            data = response.json()
            
            if 'error' in data:
                print(f"API error: {data.get('error')}")
                continue
            
            title = data.get('title', 'video')
            
            # Get download URL
            download_url = None
            
            if file_type == "mp3":
                # Get best audio stream
                audio_streams = data.get('audioStreams', [])
                if audio_streams:
                    # Sort by bitrate, get highest
                    audio_streams.sort(key=lambda x: x.get('bitrate', 0), reverse=True)
                    download_url = audio_streams[0].get('url')
                    print(f"Audio: {audio_streams[0].get('quality', 'unknown')}")
            else:
                # Get video stream (prefer 720p)
                video_streams = data.get('videoStreams', [])
                
                # First try to find 720p or 480p with audio
                for stream in video_streams:
                    quality = stream.get('quality', '')
                    if quality in ['720p', '480p', '360p'] and stream.get('videoOnly') == False:
                        download_url = stream.get('url')
                        print(f"Video: {quality}")
                        break
                
                # If no combined stream, get video only
                if not download_url:
                    for stream in video_streams:
                        quality = stream.get('quality', '')
                        if quality in ['720p', '480p', '360p']:
                            download_url = stream.get('url')
                            print(f"Video (no audio): {quality}")
                            break
                
                # Fallback to any available stream
                if not download_url and video_streams:
                    download_url = video_streams[0].get('url')
                    print(f"Fallback video stream")
            
            if not download_url:
                print("No download URL found")
                continue
            
            # Download the file
            safe_title = sanitize_filename(title)
            ext = "mp3" if file_type == "mp3" else "mp4"
            filename = f"{safe_title}.{ext}"
            file_path = os.path.join(downloads_path, filename)
            
            print(f"Downloading: {title[:50]}...")
            
            file_response = requests.get(
                download_url, 
                stream=True, 
                timeout=300,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': 'https://piped.video/',
                }
            )
            file_response.raise_for_status()
            
            total_size = int(file_response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(file_path, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            if downloaded % (1024 * 1024) < 8192:  # Log every ~1MB
                                print(f"Progress: {percent:.1f}%")
            
            print(f"Download complete: {filename}")
            
            return {
                'file_path': file_path,
                'title': title
            }
            
        except requests.exceptions.Timeout:
            last_error = "Request timeout"
            print(f"Timeout on {instance}")
            continue
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(f"Request error: {e}")
            continue
        except Exception as e:
            last_error = str(e)
            print(f"Error: {e}")
            continue
    
    raise Exception(f"All Piped instances failed. Last error: {last_error}")

def sanitize_filename(title: str) -> str:
    """Remove invalid characters from filename"""
    title = re.sub(r'[<>:"/\\|?*\n\r\t]', '', title)
    title = title[:80].strip()
    return title or "video"

def save_history(url: str, date: str, title: str, link_type: str, file_type: str) -> None:
    """Save to user history"""
    if current_user.is_authenticated:
        new_video = Video(
            title=title, 
            url=url, 
            date=date, 
            link_type=link_type, 
            file_type=file_type, 
            user_id=current_user.id
        )
        db.session.add(new_video)
        db.session.commit()
