from flask import Blueprint, redirect, render_template, request, flash, send_file, url_for, session
from flask_login import login_required, current_user
from .models import Video
from . import db

import yt_dlp
from moviepy import AudioFileClip
import mutagen

from io import BytesIO
from shutil import rmtree
import os
import zipfile
import re

views = Blueprint("views", __name__)

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
        if not url or "youtube.com" not in url and "youtu.be" not in url:
            flash("Please enter a valid YouTube URL.", category="error")
            return render_template("video.html", user=current_user)

        file_type = "mp4" if request.form["convert"] == "mp4" else "mp3"
        downloads_path = os.path.join(os.getcwd(), "temp")
        
        # Create temp directory
        os.makedirs(downloads_path, exist_ok=True)

        try:
            result = download_with_ytdlp(url, file_type, downloads_path)
            file_path = result['file_path']
            title = result['title']
            author = result['author']
        except Exception as e:
            print(f"Download error: {e}")
            flash(f"Video could not be downloaded. Error: {e}", category="error")
            return render_template("video.html", user=current_user)

        # Convert to mp3 if needed
        if file_type == "mp3" and not file_path.endswith('.mp3'):
            try:
                file_path = convert_to_mp3(file_path)
            except Exception as e:
                flash(f"Could not convert to MP3: {e}", category="error")
                return render_template("video.html", user=current_user)

        # Update metadata
        try:
            update_metadata(file_path, title, author)
        except:
            pass  # Metadata update is optional

        # Save to history
        save_history(url, date, title, "video", file_type)
        
        # Store for download page
        session["download_file_path"] = file_path
        session["download_title"] = title
        session["download_file_type"] = file_type
        
        return redirect(url_for("views.download_page"))

    session["playlist_url"] = ""
    try: url = session["video_url"]
    except: url = ""

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
    if request.method == "POST":
        playlist_url = request.form.get("url")
        date = request.form.get("date")
        session.clear()

        if not playlist_url or "playlist" not in playlist_url:
            flash("Please enter a valid YouTube playlist URL.", category="error")
            return render_template("playlist.html", user=current_user)
        
        file_type = "mp4" if request.form["convert"] == "mp4" else "mp3"
        downloads_path = os.path.join(os.getcwd(), "temp")
        
        try:
            # Get playlist info
            with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
                playlist_info = ydl.extract_info(playlist_url, download=False)
                playlist_title = playlist_info.get('title', 'Playlist')
            
            playlist_path = os.path.join(downloads_path, playlist_title)
            os.makedirs(playlist_path, exist_ok=True)
            
            for entry in playlist_info.get('entries', []):
                try:
                    video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                    result = download_with_ytdlp(video_url, file_type, playlist_path)
                    
                    if file_type == "mp3" and not result['file_path'].endswith('.mp3'):
                        result['file_path'] = convert_to_mp3(result['file_path'])
                    
                    update_metadata(result['file_path'], result['title'], result['author'], playlist_title)
                except Exception as e:
                    print(f"Error downloading: {e}")
                    continue
            
            save_history(playlist_url, date, playlist_title, "playlist", file_type)
            
            zip_file_name, memory_file = zip_folder(playlist_title, playlist_path)
            response = send_file(memory_file, download_name=zip_file_name, as_attachment=True)
            rmtree(downloads_path)
            return response
            
        except Exception as e:
            flash(f"Playlist could not be downloaded: {e}", category="error")
    
    session["video_url"] = ""
    try: url = session["playlist_url"]
    except: url = ""

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
                return render_template("history.html", user=current_user)
            except:
                db.session.rollback()
                flash("Could not clear history.", category="error")
        else:
            redirect_page = convert_video_redirect("convert")
            return redirect(url_for(redirect_page))
    
    session.clear()
    return render_template("history.html", user=current_user)

@views.route("/search", methods=["GET", "POST"])
def search():
    if request.method == "POST":
        if request.form["search"] == "video" or request.form["search"] == "playlist":
            title = request.form.get("title")
            
            # Use yt-dlp for search
            search_query = f"ytsearch10:{title}"
            with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
                search_results = ydl.extract_info(search_query, download=False)
            
            results = []
            for entry in search_results.get('entries', []):
                results.append({
                    'title': entry.get('title', ''),
                    'link': f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                    'type': 'video',
                    'duration': entry.get('duration_string', 'N/A'),
                    'viewCount': {'short': entry.get('view_count', 'N/A')},
                    'channel': {'name': entry.get('uploader', 'Unknown')}
                })
            
            return render_template("search.html", user=current_user, results=results, title=title)
        else:
            redirect_page = convert_video_redirect("search")
            return redirect(url_for(redirect_page))

    session.clear()
    return render_template("search.html", user=current_user)

## Helper Functions

def download_with_ytdlp(url: str, file_type: str, downloads_path: str) -> dict:
    """Download video/audio using yt-dlp"""
    
    # Sanitize for filename
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'outtmpl': os.path.join(downloads_path, '%(title)s.%(ext)s'),
        'restrictfilenames': False,
    }
    
    if file_type == "mp3":
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:  # mp4
        ydl_opts.update({
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
        })
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'video')
        author = info.get('uploader', 'Unknown')
        
        # Get the actual downloaded filename
        if file_type == "mp3":
            ext = "mp3"
        else:
            ext = "mp4"
        
        # Clean title for filename matching
        safe_title = ydl.prepare_filename(info)
        if file_type == "mp3":
            safe_title = os.path.splitext(safe_title)[0] + ".mp3"
        
        return {
            'file_path': safe_title,
            'title': title,
            'author': author
        }

def convert_to_mp3(file_path: str) -> str:
    """Convert audio file to mp3"""
    original_file_path = file_path
    new_file_path = os.path.splitext(file_path)[0] + ".mp3"
    
    audio = AudioFileClip(file_path)
    audio.write_audiofile(new_file_path)
    audio.close()
    
    if os.path.exists(original_file_path) and original_file_path != new_file_path:
        os.remove(original_file_path)
    
    return new_file_path

def update_metadata(file_path: str, title: str, artist: str, album: str="") -> None:
    """Update audio file metadata"""
    try:
        with open(file_path, 'r+b') as file:
            media_file = mutagen.File(file, easy=True)
            if media_file:
                media_file["title"] = title
                if album: media_file["album"] = album
                media_file["artist"] = artist
                media_file.save(file)
    except:
        pass

def convert_video_redirect(form_name: str) -> str:
    conversion_info = request.form.get(form_name)
    url, r_type = conversion_info.split()[0], conversion_info.split()[1]
    if r_type == "video":
        session["video_url"] = url
        return "views.video"
    else:
        session["playlist_url"] = url
        return "views.playlist"

def zip_folder(name: str, path: str) -> tuple:
    zip_file_name = f"{name}.zip"
    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, path)
                zipf.write(file_path, arcname)
    memory_file.seek(0)
    return zip_file_name, memory_file

def save_history(url: str, date: str, title: str, link_type: str, file_type: str) -> None:
    if current_user.is_authenticated:
        new_video = Video(title=title, url=url, date=date, link_type=link_type, file_type=file_type, user_id=current_user.id)
        db.session.add(new_video)
        db.session.commit()
