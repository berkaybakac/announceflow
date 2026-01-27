"""
AnnounceFlow - Web Panel
Flask web server with API endpoints for management.
"""
import os
import json
import functools
import subprocess
import tempfile
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.utils import secure_filename

import database as db
from player import get_player
from scheduler import get_scheduler

app = Flask(__name__)
app.secret_key = 'announceflow_secret_key_2024'

CONFIG_FILE = 'config.json'
MEDIA_FOLDER = 'media'
# Accepted upload formats (will be converted to MP3 if needed)
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'ogg', 'aiff', 'aif', 'flac', 'm4a', 'wma', 'mp2'}

# Formats that MUST be converted (not supported by any backend)
# Note: WAV/OGG work with pygame on dev, but mpg123 on Pi needs MP3
# For Pi stability, convert everything except MP3 to MP3
NEEDS_CONVERSION = {'wav', 'ogg', 'aiff', 'aif', 'flac', 'm4a', 'wma', 'mp2'}

# Ensure media directories exist
os.makedirs(os.path.join(MEDIA_FOLDER, 'music'), exist_ok=True)
os.makedirs(os.path.join(MEDIA_FOLDER, 'announcements'), exist_ok=True)


# ============ HELPERS ============

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {'volume': 80, 'admin_username': 'admin', 'admin_password': 'admin123'}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def convert_to_mp3(input_path: str, output_path: str) -> bool:
    """Convert any audio format to MP3 using ffmpeg."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        result = subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-acodec', 'libmp3lame', '-ab', '192k', '-ar', '44100',
            output_path
        ], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"ffmpeg conversion failed: {result.stderr}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg conversion timeout (>120s): {input_path}")
        return False
    except Exception as e:
        logger.error(f"ffmpeg error: {e}")
        return False

def get_audio_duration(file_path: str) -> int:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', file_path
        ], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return 0

def login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped


# ============ AUTH ROUTES ============

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        config = load_config()
        valid_user = config.get('admin_username', 'admin')
        valid_pass = config.get('admin_password', 'admin123')
        
        if username == valid_user and password == valid_pass:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Hatalı kullanıcı adı veya şifre!', 'error')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))



def _format_schedules(schedules):
    """Format schedule datetime for display (DD.MM.YYYY HH:MM)."""
    formatted = []
    for s in schedules:
        s_dict = dict(s)
        try:
            dt_str = s_dict['scheduled_datetime']
            # Handle T separator if present
            dt_str = dt_str.replace('T', ' ')
            # Parse (try with constraints)
            try:
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
            
            s_dict['display_datetime'] = dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            s_dict['display_datetime'] = s_dict['scheduled_datetime']
        formatted.append(s_dict)
    return formatted

def _format_media_files(files):
    """Format media file dates (UTC -> UTC+3) and (DD.MM.YYYY HH:MM)."""
    formatted = []
    for f in files:
        f_dict = dict(f)
        try:
            dt_str = f_dict['created_at']
            # Parse UTC time
            try:
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
            
            # Add 3 hours for Turkey Time (UTC+3) manual adjustment
            # since we know the server stores UTC
            dt_tr = dt + timedelta(hours=3)
            
            f_dict['created_at_formatted'] = dt_tr.strftime('%d.%m.%Y %H:%M')
        except Exception:
            f_dict['created_at_formatted'] = f_dict['created_at']
        formatted.append(f_dict)
    return formatted

# ============ PAGE ROUTES ============

@app.route('/')
@login_required
def index():
    """Now Playing page."""
    media_files = db.get_all_media_files()
    upcoming = db.get_pending_one_time_schedules()
    upcoming_formatted = _format_schedules(upcoming)
    return render_template('index.html', 
                         active_page='now-playing',
                         media_files=media_files,
                         upcoming_schedules=upcoming_formatted)

@app.route('/schedules/one-time')
@login_required
def one_time_schedules():
    """One-time schedules page."""
    media_files = db.get_all_media_files()
    schedules = db.get_all_one_time_schedules()
    schedules_formatted = _format_schedules(schedules)
    return render_template('one_time_schedule.html',
                         active_page='one-time',
                         media_files=media_files,
                         schedules=schedules_formatted)

@app.route('/schedules/recurring')
@login_required
def recurring_schedules():
    """Recurring schedules page."""
    media_files = db.get_all_media_files()
    schedules = db.get_all_recurring_schedules()
    return render_template('recurring_schedule.html',
                         active_page='recurring',
                         media_files=media_files,
                         schedules=schedules)

@app.route('/library')
@login_required
def library():
    """Media library page."""
    music_files = db.get_all_media_files('music')
    announcement_files = db.get_all_media_files('announcement')
    
    music_fmt = _format_media_files(music_files)
    announcements_fmt = _format_media_files(announcement_files)
    
    return render_template('library.html',
                         active_page='library',
                         music_files=music_fmt,
                         announcement_files=announcements_fmt)

@app.route('/settings')
@login_required
def settings():
    """Settings page."""
    config = load_config()
    state = db.get_playback_state()
    
    music_count = len(db.get_all_media_files('music'))
    announcement_count = len(db.get_all_media_files('announcement'))
    pending_count = len(db.get_pending_one_time_schedules())
    active_recurring = len(db.get_active_recurring_schedules())
    
    return render_template('settings.html',
                         active_page='settings',
                         volume=state.get('volume', 80),
                         total_music=music_count,
                         total_announcements=announcement_count,
                         total_schedules=pending_count + active_recurring,
                         admin_username=config.get('admin_username', 'admin'))


# ============ PLAYER API ============

@app.route('/api/now-playing')
@login_required
def api_now_playing():
    """Get current player state."""
    player = get_player()
    state = player.get_state()
    db_state = db.get_playback_state()
    state['volume'] = db_state.get('volume', 80)
    
    # Get duration from database if file is playing
    if state.get('filename'):
        media = db.get_media_by_filename(state['filename'])
        if media:
            state['duration_seconds'] = media.get('duration_seconds', 0)
    
    return jsonify(state)

@app.route('/api/play', methods=['POST'])
@login_required
def api_play():
    """Play a media file."""
    data = request.get_json() or {}
    media_id = data.get('media_id')
    
    if not media_id:
        return jsonify({'error': 'media_id required'}), 400
    
    media = db.get_media_file(media_id)
    if not media:
        return jsonify({'error': 'Media not found'}), 404
    
    player = get_player()
    success = player.play(media['filepath'])
    
    if success:
        db.update_playback_state(current_media_id=media_id, is_playing=True, position_seconds=0)
    
    return jsonify({'success': success})


@app.route('/api/pause', methods=['POST'])
@login_required
def api_pause():
    """Deprecated."""
    return jsonify({'success': False, 'error': 'Not supported'}), 405

@app.route('/api/resume', methods=['POST'])
@login_required
def api_resume():
    """Deprecated."""
    return jsonify({'success': False, 'error': 'Not supported'}), 405


@app.route('/api/stop', methods=['POST'])
@login_required
def api_stop():
    """Stop playback."""
    player = get_player()
    success = player.stop()
    db.update_playback_state(current_media_id=0, is_playing=False, position_seconds=0)
    return jsonify({'success': success})

@app.route('/api/volume', methods=['POST'])
@login_required
def api_volume():
    """Set volume level."""
    data = request.get_json() or {}
    volume = data.get('volume', 80)
    
    player = get_player()
    success = player.set_volume(volume)
    db.update_playback_state(volume=volume)
    
    return jsonify({'success': success, 'volume': volume})


# ============ MEDIA API ============

@app.route('/api/media/upload', methods=['POST'])
@login_required
def api_media_upload():
    """Upload a media file."""
    if 'file' not in request.files:
        flash('Dosya seçilmedi', 'error')
        return redirect(url_for('library'))
    
    file = request.files['file']
    media_type = request.form.get('media_type', 'music')
    
    if file.filename == '':
        flash('Dosya seçilmedi', 'error')
        return redirect(url_for('library'))
    
    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        subfolder = 'music' if media_type == 'music' else 'announcements'
        
        # Get file extension
        base, ext = os.path.splitext(original_filename)
        ext_lower = ext.lower().lstrip('.')
        
        # Check if conversion is needed
        needs_convert = ext_lower in NEEDS_CONVERSION
        
        if needs_convert:
            # Save to temp file with unique name (avoids collision)
            temp_suffix = ext  # Keep original extension
            with tempfile.NamedTemporaryFile(suffix=temp_suffix, delete=False) as tmp:
                temp_path = tmp.name
                file.save(temp_path)
            
            # Create MP3 filename
            mp3_filename = f"{base}.mp3"
            mp3_filepath = os.path.join(MEDIA_FOLDER, subfolder, mp3_filename)
            
            # Ensure unique filename
            counter = 1
            while os.path.exists(mp3_filepath):
                mp3_filename = f"{base}_{counter}.mp3"
                mp3_filepath = os.path.join(MEDIA_FOLDER, subfolder, mp3_filename)
                counter += 1
            
            # Convert to MP3
            try:
                if convert_to_mp3(temp_path, mp3_filepath):
                    # Get duration from converted file
                    duration = get_audio_duration(mp3_filepath)
                    db.add_media_file(mp3_filename, mp3_filepath, media_type, duration)
                    flash(f'{original_filename} → {mp3_filename} dönüştürüldü ve yüklendi!', 'success')
                else:
                    flash(f'{original_filename} dönüştürülemedi. ffmpeg hatası.', 'error')
            finally:
                # Always clean up temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
        else:
            # MP3 - save directly
            filepath = os.path.join(MEDIA_FOLDER, subfolder, original_filename)
            
            # Ensure unique filename
            counter = 1
            while os.path.exists(filepath):
                original_filename = f"{base}_{counter}{ext}"
                filepath = os.path.join(MEDIA_FOLDER, subfolder, original_filename)
                counter += 1
            
            file.save(filepath)
            # Get duration
            duration = get_audio_duration(filepath)
            db.add_media_file(original_filename, filepath, media_type, duration)
            flash(f'{original_filename} başarıyla yüklendi!', 'success')
    else:
        flash('Geçersiz dosya türü. Kabul edilen: MP3, WAV, OGG, AIFF, FLAC, M4A, WMA, MP2', 'error')
    
    return redirect(url_for('library'))

@app.route('/api/media/<int:media_id>/delete', methods=['POST'])
@login_required
def api_media_delete(media_id):
    """Delete a media file."""
    media = db.get_media_file(media_id)
    
    if media:
        # Delete file from disk
        if os.path.exists(media['filepath']):
            os.remove(media['filepath'])
        
        # Delete from database
        db.delete_media_file(media_id)
        flash('Dosya silindi', 'success')
    else:
        flash('Dosya bulunamadı', 'error')
    
    return redirect(url_for('library'))


# ============ SCHEDULE API ============

@app.route('/api/schedules/one-time', methods=['POST'])
@login_required
def api_add_one_time():
    """Add a one-time schedule."""
    media_id = request.form.get('media_id')
    date = request.form.get('date')
    time = request.form.get('time')
    
    if not all([media_id, date, time]):
        flash('Tüm alanları doldurun', 'error')
        return redirect(url_for('one_time_schedules'))
    
    scheduled_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    
    if scheduled_dt <= datetime.now():
        flash('Geçmiş bir tarih seçemezsiniz', 'error')
        return redirect(url_for('one_time_schedules'))
    
    db.add_one_time_schedule(int(media_id), scheduled_dt)
    flash('Plan başarıyla eklendi!', 'success')
    
    return redirect(url_for('one_time_schedules'))

@app.route('/api/schedules/one-time/<int:schedule_id>/cancel', methods=['POST'])
@login_required
def api_cancel_one_time(schedule_id):
    """Cancel a one-time schedule."""
    db.update_one_time_schedule_status(schedule_id, 'cancelled')
    flash('Plan iptal edildi', 'success')
    return redirect(url_for('one_time_schedules'))

@app.route('/api/schedules/one-time/<int:schedule_id>/delete', methods=['POST'])
@login_required
def api_delete_one_time(schedule_id):
    """Delete a one-time schedule."""
    db.delete_one_time_schedule(schedule_id)
    flash('Plan silindi', 'success')
    return redirect(url_for('one_time_schedules'))

@app.route('/api/schedules/recurring', methods=['POST'])
@login_required
def api_add_recurring():
    """Add a recurring schedule."""
    media_id = request.form.get('media_id')
    days_json = request.form.get('days_of_week', '[]')
    schedule_type = request.form.get('schedule_type', 'specific')
    
    try:
        days = json.loads(days_json)
    except:
        days = []
    
    if not media_id or not days:
        flash('Dosya ve günler gerekli', 'error')
        return redirect(url_for('recurring_schedules'))
    
    if schedule_type == 'specific':
        times_str = request.form.get('specific_times', '')
        times = [t.strip() for t in times_str.split(',') if t.strip()]
        
        if not times:
            flash('En az bir saat girin', 'error')
            return redirect(url_for('recurring_schedules'))
        
        db.add_recurring_schedule(
            int(media_id),
            days,
            times[0],  # First time as start
            specific_times=times
        )
    else:
        start_time = request.form.get('start_time', '09:00')
        end_time = request.form.get('end_time', '18:00')
        interval = int(request.form.get('interval_minutes', 60))
        
        db.add_recurring_schedule(
            int(media_id),
            days,
            start_time,
            end_time,
            interval
        )
    
    flash('Tekrarlı plan oluşturuldu!', 'success')
    return redirect(url_for('recurring_schedules'))

@app.route('/api/schedules/recurring/<int:schedule_id>/toggle', methods=['POST'])
@login_required
def api_toggle_recurring(schedule_id):
    """Toggle a recurring schedule active state."""
    schedules = db.get_all_recurring_schedules()
    current = next((s for s in schedules if s['id'] == schedule_id), None)
    
    if current:
        new_state = not current['is_active']
        db.toggle_recurring_schedule(schedule_id, new_state)
        flash('Plan durumu güncellendi', 'success')
    
    return redirect(url_for('recurring_schedules'))

@app.route('/api/schedules/recurring/<int:schedule_id>/delete', methods=['POST'])
@login_required
def api_delete_recurring(schedule_id):
    """Delete a recurring schedule."""
    db.delete_recurring_schedule(schedule_id)
    flash('Plan silindi', 'success')
    return redirect(url_for('recurring_schedules'))


# ============ SETTINGS API ============

@app.route('/api/settings/credentials', methods=['POST'])
@login_required
def api_update_credentials():
    """Update admin credentials."""
    config = load_config()
    
    username = request.form.get('username')
    password = request.form.get('password')
    
    if username:
        config['admin_username'] = username
    
    if password:
        config['admin_password'] = password
    
    save_config(config)
    flash('Yönetici bilgileri güncellendi', 'success')
    
    return redirect(url_for('settings'))


# ============ MAIN ============

if __name__ == '__main__':
    # Initialize database
    db.init_database()
    
    # Start scheduler
    scheduler = get_scheduler()
    scheduler.start()
    
    # Run web server
    from waitress import serve
    print("AnnounceFlow Web Panel çalışıyor (Port 5000)...")
    # Increase threads to prevent queue depth warnings
    serve(app, host='0.0.0.0', port=5000, threads=16, channel_timeout=10, connection_limit=100)
