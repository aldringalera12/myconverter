from flask import Blueprint, redirect, render_template, request, flash, send_file, url_for, session
from flask_login import login_required, current_user
from .models import Video
from . import db

import requests
import os
import re
from shutil import rmtree

views = Blueprint("views", __name__)

# Cobalt API endpoint
COBALT_API = "https://api.cobalt.tools/api/json"

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
            result = download_with_cobalt(url, file_type, downloads_path)
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

def download_with_cobalt(url: str, file_type: str, downloads_path: str) -> dict:
    """Download video/audio using Cobalt API"""
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }
    
    # Cobalt API payload
    payload = {
        'url': url,
        'vQuality': '1080',
        'filenamePattern': 'basic',
    }
    
    if file_type == "mp3":
        payload['isAudioOnly'] = True
        payload['aFormat'] = 'mp3'
    else:
        payload['isAudioOnly'] = False
    
    # Request from Cobalt API
    response = requests.post(COBALT_API, json=payload, headers=headers, timeout=30)
    data = response.json()
    
    if data.get('status') == 'error':
        raise Exception(data.get('text', 'Cobalt API error'))
    
    # Get download URL
    download_url = data.get('url')
    if not download_url:
        # Handle picker (multiple options)
        if data.get('status') == 'picker':
            picker = data.get('picker', [])
            if picker:
                download_url = picker[0].get('url')
        
        # Handle stream
        if data.get('status') == 'stream':
            download_url = data.get('url')
        
        # Handle redirect
        if data.get('status') == 'redirect':
            download_url = data.get('url')
    
    if not download_url:
        raise Exception("Could not get download URL from Cobalt")
    
    # Get filename from URL or generate one
    title = extract_video_title(url) or "video"
    safe_title = sanitize_filename(title)
    ext = "mp3" if file_type == "mp3" else "mp4"
    filename = f"{safe_title}.{ext}"
    file_path = os.path.join(downloads_path, filename)
    
    # Download the file
    print(f"Downloading from Cobalt: {download_url[:50]}...")
    file_response = requests.get(download_url, stream=True, timeout=300)
    file_response.raise_for_status()
    
    with open(file_path, 'wb') as f:
        for chunk in file_response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    
    print(f"Downloaded: {filename}")
    
    return {
        'file_path': file_path,
        'title': title
    }

def extract_video_title(url: str) -> str:
    """Try to get video title from YouTube"""
    try:
        # Try to get title from YouTube page
        response = requests.get(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        match = re.search(r'<title>(.+?) - YouTube</title>', response.text)
        if match:
            return match.group(1)
    except:
        pass
    
    # Extract video ID as fallback
    video_id = None
    if 'youtu.be/' in url:
        video_id = url.split('youtu.be/')[-1].split('?')[0]
    elif 'v=' in url:
        video_id = url.split('v=')[-1].split('&')[0]
    
    return video_id or "video"

def sanitize_filename(title: str) -> str:
    """Remove invalid characters from filename"""
    # Remove invalid characters
    title = re.sub(r'[<>:"/\\|?*]', '', title)
    # Limit length
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
