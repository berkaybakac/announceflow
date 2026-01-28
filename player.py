"""
AnnounceFlow - Audio Player
Raspberry Pi optimized audio playback using mpg123.
Falls back to pygame for development on Mac/Windows.
"""
import os
import logging
import subprocess
import threading
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Detect available audio backend
def _detect_backend():
    """Detect best available audio backend."""
    # Check for mpg123 first (preferred for Pi)
    try:
        result = subprocess.run(['which', 'mpg123'], capture_output=True, text=True)
        if result.returncode == 0:
            return 'mpg123'
    except:
        pass
    
    # Fall back to pygame
    try:
        import pygame
        pygame.mixer.init()
        return 'pygame'
    except:
        pass
    
    return None

AUDIO_BACKEND = _detect_backend()
logger.info(f"Audio backend: {AUDIO_BACKEND}")


class AudioPlayer:
    """Cross-platform audio player with mpg123 (Pi) or pygame (dev) backend."""
    
    def __init__(self, on_track_end: Optional[Callable] = None):
        self.current_file: Optional[str] = None
        self.is_playing: bool = False
        self.is_paused: bool = False
        self._volume: int = 80  # 0-100
        self._position: float = 0.0
        self._duration: float = 0.0
        self._started_at: float = 0.0  # Unix timestamp when playback started
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._user_on_track_end = on_track_end
        
        # Playlist support
        self._playlist: list = []  # List of file paths
        self._playlist_index: int = -1  # Current track index
        self._playlist_loop: bool = True  # Loop playlist when reaching end
        self._playlist_active: bool = False  # Whether playlist mode is on
        
        # Initialize pygame if that's our backend
        if AUDIO_BACKEND == 'pygame':
            import pygame
            pygame.mixer.music.set_volume(self._volume / 100.0)
    
    def on_track_end(self):
        """Internal track end handler - advances playlist or calls user callback."""
        if self._playlist_active and len(self._playlist) > 0:
            self.play_next()
        elif self._user_on_track_end:
            self._user_on_track_end()
    
    def set_playlist(self, file_paths: list, loop: bool = True) -> bool:
        """Set a playlist of files to play sequentially."""
        if not file_paths:
            return False
        
        self._playlist = file_paths
        self._playlist_index = -1
        self._playlist_loop = loop
        self._playlist_active = True
        logger.info(f"Playlist set with {len(file_paths)} tracks, loop={loop}")
        return True
    
    def play_playlist(self) -> bool:
        """Start playing the playlist from the beginning."""
        if not self._playlist:
            return False
        
        self._playlist_index = 0
        self._playlist_active = True
        return self.play(self._playlist[0])
    
    def play_next(self) -> bool:
        """Play the next track in the playlist."""
        if not self._playlist:
            return False
        
        next_index = self._playlist_index + 1
        
        if next_index >= len(self._playlist):
            if self._playlist_loop:
                next_index = 0
            else:
                self._playlist_active = False
                logger.info("Playlist ended (no loop)")
                return False
        
        self._playlist_index = next_index
        logger.info(f"Playing next track: {next_index + 1}/{len(self._playlist)}")
        return self.play(self._playlist[next_index])
    
    def stop_playlist(self):
        """Stop the playlist and clear it."""
        self._playlist_active = False
        self._playlist = []
        self._playlist_index = -1
        self.stop()
    
    def get_playlist_state(self) -> dict:
        """Get current playlist state."""
        return {
            'active': self._playlist_active,
            'tracks': len(self._playlist),
            'current_index': self._playlist_index,
            'loop': self._playlist_loop,
            'current_file': self._playlist[self._playlist_index] if 0 <= self._playlist_index < len(self._playlist) else None
        }
    
    def play(self, file_path: str, start_position: float = 0.0) -> bool:
        """
        Play an audio file.
        
        Args:
            file_path: Path to the audio file
            start_position: Start position in seconds (ignored for mpg123)
        
        Returns:
            True if playback started successfully
        """
        # Resolve relative path
        if not os.path.isabs(file_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(base_dir, file_path)
        
        if not os.path.exists(file_path):
            logger.error(f"Audio file not found: {file_path}")
            return False
        
        # Stop any current playback
        self.stop()
        
        with self._lock:
            try:
                if AUDIO_BACKEND == 'mpg123':
                    return self._play_mpg123(file_path)
                elif AUDIO_BACKEND == 'pygame':
                    return self._play_pygame(file_path, start_position)
                else:
                    logger.error("No audio backend available")
                    return False
            except Exception as e:
                logger.error(f"Error playing audio: {e}")
                return False
    
    def _play_mpg123(self, file_path: str) -> bool:
        """Play using mpg123 (Pi optimized)."""
        try:
            # Start mpg123 process with FIXED max scale (32768)
            # Volume is controlled SOLELY by ALSA amixer to avoid double-volume curve
            # Use -a plughw:2,0 to force output to headphone jack (Pi 4 specific)
            self._process = subprocess.Popen(
                ['mpg123', '-a', 'plughw:2,0', '-q', '--scale', '32768', file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            self.current_file = file_path
            self.is_playing = True
            self._started_at = time.time()  # Record start time
            self.is_paused = False
            
            logger.info(f"Playing (mpg123): {os.path.basename(file_path)}")
            
            # Start monitor thread
            self._start_monitor_mpg123()
            
            return True
        except Exception as e:
            logger.error(f"mpg123 error: {e}")
            return False
    
    def _play_pygame(self, file_path: str, start_position: float) -> bool:
        """Play using pygame (dev fallback)."""
        import pygame
        
        if pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
        
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play(start=start_position)
        
        self.current_file = file_path
        self.is_playing = True
        self.is_paused = False
        self._position = start_position
        
        # Get duration
        try:
            sound = pygame.mixer.Sound(file_path)
            self._duration = sound.get_length()
        except:
            self._duration = 0.0
        
        self._started_at = time.time()  # Record start time
        
        logger.info(f"Playing (pygame): {os.path.basename(file_path)}")
        
        # Start monitor
        self._start_monitor_pygame()
        
        return True
    
    
    def stop(self) -> bool:
        """Stop playback safely."""
        # 1. Update state FIRST to prevent UI race conditions
        with self._lock:
            self.is_playing = False
            self.is_paused = False
            self.current_file = None
            self._position = 0.0
            self._started_at = 0.0  # Reset start time
            self._stop_event.set()

        # 2. Kill process safely
        if AUDIO_BACKEND == 'mpg123' and self._process:
            try:
                # Use SIGKILL for immediate termination
                self._process.kill()
                self._process.wait(timeout=0.5)
            except Exception as e:
                logger.debug(f"Process kill ignored: {e}")
            finally:
                self._process = None
                
        elif AUDIO_BACKEND == 'pygame':
            import pygame
            pygame.mixer.music.stop()
        
        logger.info("Playback stopped")
        return True
    
    # Pause/Resume removed for stability with mpg123
    def pause(self) -> bool: return False
    def resume(self) -> bool: return False


    
    def set_volume(self, volume: int) -> bool:
        """Set volume level (0-100) using logarithmic mapping for natural feel."""
        volume = max(0, min(100, volume))
        self._volume = volume
        
        if AUDIO_BACKEND == 'pygame':
            import pygame
            pygame.mixer.music.set_volume(volume / 100.0)
        
        # Always try to set hardware volume on Pi (mpg123 or others)
        try:
            # Map UI volume (0-100) to Hardware volume (55-100)
            # ALSA dB scale is not linear, so we clamp the bottom end
            # Formula: 55 + (ui/100)^1.6 * 45
            if volume <= 0:
                hw_volume = 0
            else:
                hw_volume = int(round(55 + (volume / 100.0) ** 1.6 * 45))
            
            subprocess.run(['amixer', '-c', '2', 'set', 'PCM', f'{hw_volume}%', 'unmute'], 
                           stdout=subprocess.DEVNULL, 
                           stderr=subprocess.DEVNULL)
            logger.info(f"Volume set to: {volume}% (HW: {hw_volume}%)")
        except Exception as e:
            logger.debug(f"Failed to set hardware volume: {e}")
            
        return True
    
    def get_volume(self) -> int:
        """Get current volume level."""
        return self._volume
    
    def get_position(self) -> float:
        """Get current playback position in seconds."""
        if AUDIO_BACKEND == 'pygame' and self.is_playing:
            import pygame
            return pygame.mixer.music.get_pos() / 1000.0
        return self._position
    
    def get_duration(self) -> float:
        """Get duration of current track in seconds."""
        return self._duration
    
    def get_state(self) -> dict:
        """Get current player state."""
        state = {
            'current_file': self.current_file,
            'filename': os.path.basename(self.current_file) if self.current_file else None,
            'is_playing': self.is_playing,
            'is_paused': self.is_paused,
            'position': self.get_position(),
            'duration': self._duration,
            'started_at': int(self._started_at * 1000) if self._started_at else 0,  # Unix ms
            'volume': self._volume,
            'backend': AUDIO_BACKEND
        }
        # Add playlist info
        state['playlist'] = self.get_playlist_state()
        return state
    
    def _start_monitor_mpg123(self):
        """Monitor mpg123 process for completion."""
        self._stop_event.clear()
        
        def monitor():
            while not self._stop_event.is_set():
                if self._process and self._process.poll() is not None:
                    # Process ended
                    with self._lock:
                        self.is_playing = False
                        self.current_file = None
                    
                    if self.on_track_end:
                        self.on_track_end()
                    
                    logger.info("Track ended (mpg123)")
                    break
                time.sleep(0.5)
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()
    
    def _start_monitor_pygame(self):
        """Monitor pygame playback for completion."""
        self._stop_event.clear()
        
        def monitor():
            import pygame
            while not self._stop_event.is_set():
                if not pygame.mixer.music.get_busy() and self.is_playing and not self.is_paused:
                    with self._lock:
                        self.is_playing = False
                        self.current_file = None
                        self._position = 0.0
                    
                    if self.on_track_end:
                        self.on_track_end()
                    
                    logger.info("Track ended (pygame)")
                    break
                time.sleep(0.5)
        
        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()


# Singleton instance
_player_instance: Optional[AudioPlayer] = None

def get_player() -> AudioPlayer:
    """Get the singleton player instance."""
    global _player_instance
    if _player_instance is None:
        _player_instance = AudioPlayer()
    return _player_instance


if __name__ == '__main__':
    # Test the player
    logging.basicConfig(level=logging.INFO)
    print(f"Audio backend: {AUDIO_BACKEND}")
    player = get_player()
    print(f"Volume: {player.get_volume()}%")
    print("State:", player.get_state())
