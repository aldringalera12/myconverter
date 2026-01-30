from flask import Blueprint, redirect, render_template, request, flash, send_file, url_for, session
from flask_login import login_required, current_user
from .models import Video
from . import db

from pytubefix import YouTube, Playlist
from youtubesearchpython import VideosSearch, PlaylistsSearch
from moviepy import VideoFileClip, AudioFileClip
import mutagen

from io import BytesIO
from shutil import rmtree
import os
import zipfile
import subprocess
from imageio_ffmpeg import get_ffmpeg_exe

views = Blueprint("views", __name__)

## Pages

@views.route("/")
def home():
    # Clears the session data and redirects to video conversion page: "/video"
    session.clear()
    return redirect(url_for("views.video"))

@views.route("/video", methods=["GET", "POST"])
def video():
    # Check if post request
    if request.method == "POST":
        url = request.form.get("url")
        date = request.form.get("date")
        
        session.clear()

        # Try converting a url to a downloadable video
        try:
            yt = YouTube(url, client='WEB')
        except Exception:
            if "playlist?" in url:
                flash("Playlists can only be converted on the playlist page.", category="error")
            else:
                flash("Video URL is not valid.", category="error")
            return render_template("video.html", user=current_user)
        
        # Assign the file type and donwloads path
        file_type = "mp4" if request.form["convert"] == "mp4" else "mp3"
        downloads_path = os.path.join(os.getcwd(), "temp")

        # Try downloading the converted video
        try:
            video = download_video(yt, file_type, downloads_path, True)
        except Exception as e:
            print(f"Download error: {e}")
            flash(f"Video could not be downloaded. Error: {e}", category="error")
            return render_template("video.html", user=current_user)

        file_path = os.path.join(downloads_path, video.default_filename)

        # Convert to mp3
        try:
            if file_type == "mp3":
                file_path_mp3 = file_path.replace(file_path.split(".")[1], "mp3")
                if os.path.exists(file_path_mp3):
                    os.remove(file_path_mp3)
                
                file_path = convert_to_mp3_with_metadata(file_path)
        except Exception:
            flash("Video could not be converted to an MP3 format successfully. File cannot be found or already exists.", category="error")
            return render_template("video.html", user=current_user)

        # Update file metadata
        update_metadata(file_path, yt.title, yt.author)

        # Save conversion to user history
        save_history(url, date, video.title, "video", file_type)
        
        # Store file info in session for download page
        session["download_file_path"] = file_path
        session["download_title"] = yt.title
        session["download_file_type"] = file_type
        
        return redirect(url_for("views.download_page"))

    # Clear playlist url session data and try to retrieve video url session data
    session["playlist_url"] = ""
    try: url = session["video_url"]
    except Exception: url = ""

    return render_template("video.html", user=current_user, url=url)

@views.route("/download")
def download_page():
    # Show download page with file info
    file_path = session.get("download_file_path")
    title = session.get("download_title")
    file_type = session.get("download_file_type")
    
    if not file_path or not os.path.exists(file_path):
        flash("No file available for download.", category="error")
        return redirect(url_for("views.video"))
    
    return render_template("download.html", user=current_user, title=title, file_type=file_type)

@views.route("/download-file")
def download_file():
    # Send the file for download
    file_path = session.get("download_file_path")
    
    if not file_path or not os.path.exists(file_path):
        flash("File not found.", category="error")
        return redirect(url_for("views.video"))
    
    try:
        downloads_path = os.path.dirname(file_path)
        downloaded_file = send_file(path_or_file=file_path, as_attachment=True)
        return downloaded_file
    except Exception:
        flash("Could not send file for download.", category="error")
        return redirect(url_for("views.video"))

@views.route("/playlist", methods=["GET", "POST"])
def playlist():
    # Check if post request
    if request.method == "POST":
        playlist_url = request.form.get("url")
        date = request.form.get("date")

        session.clear()

        # Try converting a url into a playlist data object
        try:
            playlist = Playlist(playlist_url)
        except Exception:
            flash("Playlist URL is not valid.", category="error")
            return render_template("playlist.html", user=current_user)
        
        # Assign the file type
        file_type = "mp4" if request.form["convert"] == "mp4" else "mp3"
        
        # Try downloading all the files in the playlist
        downloads_path = os.path.join(os.getcwd(), "temp")
        playlist_path = os.path.join(downloads_path, playlist.title)

        for index, url in enumerate(playlist):
            try:
                yt = YouTube(url, client='WEB')
                video = download_video(yt, file_type, playlist_path, False)
                file_path = os.path.join(playlist_path, video.default_filename)


                if file_type == "mp3":
                    file_path_mp3 = file_path.replace("mp4", "mp3")
                    if os.path.exists(file_path_mp3):
                        os.remove(file_path_mp3)
                    
                    file_path = convert_to_mp3_with_metadata(file_path)

                # Update file metadata
                update_metadata(file_path, yt.title, yt.author, playlist.title)
            except Exception:
                print(f"There was an error converting {yt.title}. Video may not exist!")
                continue

            # Set playlist length. If there is only one video, default to one
            try: playlist_len = playlist.length
            except Exception: playlist_len = 1
            
            # Debug the video download progress
            debug_video_progress(yt, video, file_type, f"({index + 1} of {playlist_len}): ")
        
        # Save conversion to user history
        save_history(playlist_url, date, playlist.title, "playlist", file_type)

        # Try zipping the playlist folder with the downloaded videos and sending the zip file to the web browser
        try:
            zip_file_name, memory_file = zip_folder(playlist.title, playlist_path)
            downloaded_file = send_file(memory_file, attachment_filename=zip_file_name, as_attachment=True)
            rmtree(downloads_path)
            return downloaded_file
        except Exception:
            flash("Playlist converted successfully, but the zipped folder couldn't be sent to the browser! Saved to temporary folder.", category="warning")
            print(f"Folder stored at: {downloads_path}")
    
    # Clear video url session data and try to retrieve playlist url session data
    session["video_url"] = ""
    try: url = session["playlist_url"]
    except Exception: url = ""

    return render_template("playlist.html", user=current_user, url=url)

@views.route("/history", methods=["GET", "POST"])
@login_required
def history():
    # Check if post request
    if request.method == "POST":
        # If the button pressed is not a convert button; i.e. the button pressed was clear history
        if "convert" not in request.form:
            # Try clearing user history
            try:
                db.session.query(Video).delete()
                db.session.commit()
                flash("Cleared History", category="success")
                return render_template("history.html", user=current_user)
            except Exception:
                db.session.rollback()
                flash("Could not clear history.", category="error")
        else: # If the button pressed was a convert button
            redirect_page = convert_video_redirect("convert")
            return redirect(url_for(redirect_page))
    
    # Clear the session data
    session.clear()
    return render_template("history.html", user=current_user)

@views.route("/search", methods=["GET", "POST"])
def search():
    # Check if post request
    if request.method == "POST":
        # If either the search video or search playlist buttons were pressed
        if request.form["search"] == "video" or request.form["search"] == "playlist":
            title = request.form.get("title")

            # Display top 10 search results
            if request.form["search"] == "video":
                results = VideosSearch(title, limit=10).result()["result"]
            elif request.form["search"] == "playlist":
                results = PlaylistsSearch(title, limit=10).result()["result"]
            
            return render_template("search.html", user=current_user, results=results, title=title)
        else: # If the button pressed was a convert button
            redirect_page = convert_video_redirect("search")
            return redirect(url_for(redirect_page))

    # Clear the session data
    session.clear()
    return render_template("search.html", user=current_user)

## Functions

def convert_to_mp3_with_metadata(file_path: str) -> str:
    # Use moviepy to convert audio/video to mp3 with metadata support. Delete original afterwards
    original_file_path = file_path
    file_ext = os.path.splitext(file_path)[1].lower()
    new_file_path = os.path.splitext(file_path)[0] + ".mp3"
    
    # Use AudioFileClip for audio-only files, VideoFileClip for video files
    if file_ext in ['.m4a', '.webm', '.ogg', '.wav', '.flac']:
        audio = AudioFileClip(file_path)
        audio.write_audiofile(new_file_path)
        audio.close()
    else:
        video = VideoFileClip(file_path)
        video.audio.write_audiofile(new_file_path)
        video.close()
    
    os.remove(original_file_path)
    return new_file_path

def update_metadata(file_path: str, title: str, artist: str, album: str="") -> None:
    # Update the file metadata according to YouTube video details
    with open(file_path, 'r+b') as file:
        media_file = mutagen.File(file, easy=True)
        media_file["title"] = title
        if album: media_file["album"] = album
        media_file["artist"] = artist
        media_file.save(file)

def convert_video_redirect(form_name: str) -> str:
    # Save video url in session data and redirect to corresponding page
    conversion_info = request.form.get(form_name)
    url, r_type = conversion_info.split()[0], conversion_info.split()[1]
    if r_type == "video":
        session["video_url"] = url
        redirect_page = "views.video"
    else:
        session["playlist_url"] = url
        redirect_page = "views.playlist"
    return redirect_page

def zip_folder(name: str, path: str) -> tuple[str, BytesIO]:
    # Zip a folder
    zip_file_name = f"{name}.zip"
    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(path):
            for file in files:
                zipf.write(os.path.join(root, file))
            
    memory_file.seek(0)
    return zip_file_name, memory_file

def download_video(yt: YouTube, file_type: str, downloads_path: str, debug: bool=False):
    # Download a video and debug progress
    if file_type == "mp4":
        # Get highest quality video stream (may be video-only)
        video_stream = yt.streams.filter(adaptive=True, file_extension='mp4', only_video=True).order_by('resolution').desc().first()
        # Get highest quality audio stream
        audio_stream = yt.streams.filter(adaptive=True, only_audio=True).order_by('abr').desc().first()
        
        if debug:
            print(f"Fetching \"{yt.title}\"")
            print(f"[Video: {video_stream.resolution}, Audio: {audio_stream.abr}, Author: {yt.author}]\n")
        
        # Download both streams
        video_path = video_stream.download(downloads_path, filename_prefix="video_")
        audio_path = audio_stream.download(downloads_path, filename_prefix="audio_")
        
        # Merge video and audio using ffmpeg
        safe_title = "".join(c for c in yt.title if c.isalnum() or c in (' ', '-', '_')).strip()
        output_path = os.path.join(downloads_path, f"{safe_title}.mp4")
        
        ffmpeg_path = get_ffmpeg_exe()
        subprocess.run([
            ffmpeg_path, '-i', video_path, '-i', audio_path,
            '-c:v', 'copy', '-c:a', 'aac', '-y', output_path
        ], capture_output=True)
        
        # Clean up temp files
        os.remove(video_path)
        os.remove(audio_path)
        
        # Return a simple object with the filename
        class VideoResult:
            def __init__(self, filename):
                self.default_filename = filename
                self.title = yt.title
        
        return VideoResult(f"{safe_title}.mp4")
    else:
        video = yt.streams.filter(only_audio=True).get_audio_only()

        if debug:
            debug_video_progress(yt, video, file_type)

        video.download(downloads_path)
        return video

def save_history(url: str, date: str, title: str, link_type: str, file_type: str) -> None:
    # Save user history data in the generated database
    if current_user.is_authenticated:
        new_video = Video(title=title, url=url, date=date, link_type=link_type, file_type=file_type, user_id=current_user.id)
        db.session.add(new_video)
        db.session.commit()

## Debug functions

def debug_video_progress(yt: YouTube, video, file_type: str, extra_info: str=""):
    highest_res = f", Highest Resolution: {video.resolution}" if file_type == "mp4" else ""
    print(f"Fetching {extra_info}\"{video.title}\"")
    print(f"[File size: {round(video.filesize * 0.000001, 2)} MB{highest_res}, Author: {yt.author}]\n")