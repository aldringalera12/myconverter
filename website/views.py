from flask import Blueprint, redirect, render_template, request, flash, send_file, url_for, session
from flask_login import login_required, current_user
from .models import Video
from . import db

import requests
import os
import re
from shutil import rmtree

views = Blueprint("views", __name__)

# Invidious public instances (fallback list)
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.jing.rocks",
    "https://yt.artemislena.eu",
    "https://invidious.privacyredirect.com",
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

        # Validate URL
        if not url or ("youtube.com" not in url and "youtu.be" not in url):
            flash("Please enter a valid YouTube URL.", category="error")
            return render_template("video.html", user=current_user)

        file_type = "mp4" if request.form["convert"] == "mp4" else "mp3"
        downloads_path = os.path.join(os.getcwd(), "temp")
        os.makedirs(downloads_path, exist_ok=True)

        try:
            result = download_video_invidious(url, file_type, downloads_path)
            file_path = result['file_path']
            title = result['title']
        except Exception as e:
            print(f"Download error: {e}")
            flash(f"Video could not be downloaded. Error: {e}", category="error")
            return render_template("video.html", user=current_user)

        # Save to history
        save_history(url, date, title, "video", file_type)
        
        # Store for download page
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
    return None

def download_video_invidious(url: str, file_type: str, downloads_path: str) -> dict:
    """Download video using Invidious API"""
    
    video_id = extract_video_id(url)
    if not video_id:
        raise Exception("Could not extract video ID from URL")
    
    print(f"Downloading video ID: {video_id}")
    
    # Try each Invidious instance
    last_error = None
    for instance in INVIDIOUS_INSTANCES:
        try:
            print(f"Trying instance: {instance}")
            
            # Get video info
            api_url = f"{instance}/api/v1/videos/{video_id}"
            response = requests.get(api_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            if response.status_code != 200:
                continue
                
            data = response.json()
            title = data.get('title', 'video')
            
            # Get download URL
            download_url = None
            
            if file_type == "mp3":
                # Get audio stream
                adaptive_formats = data.get('adaptiveFormats', [])
                for fmt in adaptive_formats:
                    if 'audio' in fmt.get('type', ''):
                        download_url = fmt.get('url')
                        break
            else:
                # Get video stream (prefer 720p or best available)
                formats = data.get('formatStreams', [])
                for fmt in formats:
                    if fmt.get('quality') in ['720p', '1080p', '480p', '360p']:
                        download_url = fmt.get('url')
                        break
                
                # Fallback to adaptive formats
                if not download_url:
                    adaptive_formats = data.get('adaptiveFormats', [])
                    for fmt in adaptive_formats:
                        if 'video' in fmt.get('type', '') and 'mp4' in fmt.get('type', ''):
                            download_url = fmt.get('url')
                            break
            
            if not download_url:
                continue
            
            # Download the file
            safe_title = sanitize_filename(title)
            ext = "mp3" if file_type == "mp3" else "mp4"
            filename = f"{safe_title}.{ext}"
            file_path = os.path.join(downloads_path, filename)
            
            print(f"Downloading: {title}")
            file_response = requests.get(download_url, stream=True, timeout=300, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            file_response.raise_for_status()
            
            with open(file_path, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            print(f"Downloaded successfully: {filename}")
            
            return {
                'file_path': file_path,
                'title': title
            }
            
        except Exception as e:
            last_error = e
            print(f"Instance {instance} failed: {e}")
            continue
    
    raise Exception(f"All Invidious instances failed. Last error: {last_error}")

def sanitize_filename(title: str) -> str:
    """Remove invalid characters from filename"""
    title = re.sub(r'[<>:"/\\|?*]', '', title)
    title = title[:100].strip()
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
