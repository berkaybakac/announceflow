"""
AnnounceFlow - Audio Player
Raspberry Pi optimized audio playback using mpg123.
Falls back to pygame for development on Mac/Windows.
"""
import platform
import os
import logging
import subprocess
import threading
import time
from typing import Optional, Callable

import database as db
from logger import log_play, log_volume, log_error

logger = logging.getLogger(__name__)


# Detect available audio backend
def _detect_backend():
    """Detect best available audio backend."""
    # On macOS, force pygame for better development experience
    if platform.system() == "Darwin":
        try:
            import pygame

            pygame.mixer.init()
            return "pygame"
        except (ImportError, Exception):
            pass

    # Check for mpg123 first (preferred for Pi)
    try:
        result = subprocess.run(["which", "mpg123"], capture_output=True, text=True)
        if result.returncode == 0:
            return "mpg123"
    except (subprocess.SubprocessError, OSError):
        pass

    # Fall back to pygame
    try:
        import pygame

        pygame.mixer.init()
        return "pygame"
    except (ImportError, Exception):
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
        self._state_lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._playback_session: int = 0
        self._user_on_track_end = on_track_end

        # Playlist support
        self._playlist: list = []  # List of file paths
        self._playlist_index: int = -1  # Current track index
        self._playlist_loop: bool = True  # Loop playlist when reaching end
        self._playlist_active: bool = False  # Whether playlist mode is on
        self._alsa_device_candidates: list = []
        self._alsa_card_candidates: list = []

        if platform.system() == "Linux":
            self._alsa_device_candidates = self._build_alsa_device_candidates()
            self._alsa_card_candidates = self._build_alsa_card_candidates()

        # Initialize pygame if that's our backend
        if AUDIO_BACKEND == "pygame":
            import pygame

            pygame.mixer.music.set_volume(self._volume / 100.0)

    def _build_alsa_device_candidates(self) -> list:
        """Build ALSA device candidates for mpg123 playback."""
        candidates = []

        env_device = os.environ.get("ANNOUNCEFLOW_ALSA_DEVICE", "").strip()
        env_card = os.environ.get("ANNOUNCEFLOW_ALSA_CARD", "").strip()

        if env_device:
            candidates.append(env_device)

        if env_card:
            if env_card.startswith(("plughw:", "hw:")):
                if env_card.startswith("hw:"):
                    card_part = env_card.split(":", 1)[1]
                    candidates.append(f"plughw:{card_part}")
                else:
                    candidates.append(env_card)
            else:
                card_part = env_card if "," in env_card else f"{env_card},0"
                candidates.append(f"plughw:{card_part}")

        # Keep existing default preference first, then safe fallbacks
        candidates.extend(["plughw:2,0", "plughw:0,0", "default"])

        deduped = []
        for item in candidates:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    def _build_alsa_card_candidates(self) -> list:
        """Build ALSA card candidates for amixer volume control."""
        candidates = []
        env_card = os.environ.get("ANNOUNCEFLOW_ALSA_CARD", "").strip()

        if env_card:
            if env_card.startswith(("plughw:", "hw:")):
                # Convert hw:2,0 -> 2
                tail = env_card.split(":", 1)[1]
                card_idx = tail.split(",", 1)[0]
                if card_idx:
                    candidates.append(card_idx)
            else:
                candidates.append(env_card)

        # Preserve current default card first, then common alternatives
        candidates.extend(["2", "0", "1"])

        deduped = []
        for item in candidates:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    def _run_amixer_for_control(self, control: str, value_args: list) -> bool:
        """Try amixer with candidate cards for a given control."""
        if platform.system() != "Linux":
            return True

        for card in self._alsa_card_candidates:
            result = subprocess.run(
                ["amixer", "-c", card, "set", control, *value_args],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True

        # Fallback: let amixer choose default card
        result = subprocess.run(
            ["amixer", "set", control, *value_args],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _set_hardware_volume(self, value_args: list) -> bool:
        """Set hardware volume trying PCM first, then Master."""
        return self._run_amixer_for_control(
            "PCM", value_args
        ) or self._run_amixer_for_control("Master", value_args)

    def _detect_audio_codec(self, file_path: str) -> str:
        """Detect primary audio codec via ffprobe (best effort)."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=codec_name",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode == 0:
                return result.stdout.strip().lower()
        except (subprocess.SubprocessError, OSError):
            pass
        return ""

    def _ensure_mpg123_compatible(self, file_path: str) -> bool:
        """
        Ensure an .mp3 file is actually MP3-coded.
        Some clients upload AAC/MP4 streams renamed as .mp3; mpg123 cannot decode them.
        """
        if AUDIO_BACKEND != "mpg123":
            return True
        if platform.system() != "Linux":
            return True
        if not file_path.lower().endswith(".mp3"):
            return True

        codec = self._detect_audio_codec(file_path)
        if not codec or codec == "mp3":
            return True

        logger.warning(
            f"Non-MP3 codec detected in .mp3 file ({codec}), auto-converting: {os.path.basename(file_path)}"
        )
        temp_path = f"{file_path}.af_tmp.mp3"
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    file_path,
                    "-acodec",
                    "libmp3lame",
                    "-ab",
                    "192k",
                    "-ar",
                    "44100",
                    temp_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
            if result.returncode != 0:
                logger.error(
                    f"Auto-conversion failed for incompatible mp3 file: {os.path.basename(file_path)}"
                )
                return False

            converted_codec = self._detect_audio_codec(temp_path)
            if converted_codec != "mp3":
                logger.error(
                    f"Auto-conversion produced non-mp3 codec ({converted_codec}) for: {os.path.basename(file_path)}"
                )
                return False

            os.replace(temp_path, file_path)
            logger.info(f"Auto-converted for mpg123 compatibility: {os.path.basename(file_path)}")
            return True
        except subprocess.TimeoutExpired:
            logger.error(
                f"[ANONS_KAYIP] Ses dosyası dönüştürme zaman aşımına uğradı (60s) — "
                f"dosya uyumsuz formatta yüklenmiş olabilir (WhatsApp/telefon kaydı): "
                f"{os.path.basename(file_path)}. "
                f"Bu süre zarfında zamanlanmış anonslar kaçmış olabilir."
            )
            log_error(
                "mp3_conversion_timeout",
                {"file": os.path.basename(file_path), "timeout_seconds": 60},
            )
            return False
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Auto-conversion error for {os.path.basename(file_path)}: {e}")
            return False
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def on_track_end(self):
        """Internal track end handler - advances playlist or calls user callback."""
        if self._playlist_active and len(self._playlist) > 0:
            self.play_next()
        elif self._user_on_track_end:
            self._user_on_track_end()

    def apply_playlist_state(
        self,
        *,
        playlist: Optional[list] = None,
        index: Optional[int] = None,
        loop: Optional[bool] = None,
        runtime_active: Optional[bool] = None,
        db_active: Optional[bool] = None,
        play_next: bool = False,
    ) -> bool:
        """Single orchestration point for playlist state transitions."""
        with self._state_lock:
            playlist_value = list(self._playlist) if playlist is None else list(playlist)
            index_value = self._playlist_index if index is None else int(index)
            loop_value = self._playlist_loop if loop is None else bool(loop)
            runtime_active_value = (
                self._playlist_active if runtime_active is None else bool(runtime_active)
            )

            self._playlist = playlist_value
            self._playlist_index = index_value
            self._playlist_loop = loop_value
            self._playlist_active = runtime_active_value

        if db_active is not None:
            db.save_playlist_state(
                playlist=playlist_value,
                index=index_value,
                loop=loop_value,
                active=bool(db_active),
            )

        if play_next:
            return self.play_next()
        return True

    def set_playlist(self, file_paths: list, loop: bool = True, shuffle: bool = False) -> bool:
        """Set a playlist of files to play sequentially."""
        if not file_paths:
            return False

        if shuffle:
            import random
            file_paths = list(file_paths)
            random.shuffle(file_paths)

        self._playlist = file_paths
        self._playlist_index = -1
        self._playlist_loop = loop
        self._playlist_active = True

        # Persist to database for auto-resume on restart
        db.save_playlist_state(playlist=file_paths, index=-1, loop=loop, active=True)

        logger.info(f"Playlist set with {len(file_paths)} tracks, loop={loop}, shuffle={shuffle}")
        log_play("playlist_set", {"tracks": len(file_paths), "loop": loop, "shuffle": shuffle})
        return True

    def play_playlist(self) -> bool:
        """Start playing the playlist from the beginning."""
        if not self._playlist:
            return False

        self._playlist_index = -1
        self._playlist_active = True

        # Persist to database
        db.save_playlist_state(index=-1, active=True)

        return self.play_next()

    def play_next(self) -> bool:
        """Play the next track in the playlist."""
        if not self._playlist:
            return False

        total_tracks = len(self._playlist)
        checked = 0
        cursor = self._playlist_index

        while checked < total_tracks:
            next_index = cursor + 1
            if next_index >= total_tracks:
                if self._playlist_loop:
                    next_index = 0
                else:
                    self._playlist_active = False
                    db.save_playlist_state(active=False)
                    logger.info("Playlist ended (no loop)")
                    log_play(
                        "playlist_end",
                        {"reason": "no_loop", "total_tracks": total_tracks},
                    )
                    return False

            track_path = self._playlist[next_index]
            track_name = os.path.basename(track_path)

            if not os.path.exists(track_path):
                logger.error(f"Playlist track missing on disk, skipping: {track_name}")
                log_error("playlist_track_missing", {"file": track_name, "index": next_index + 1})
                cursor = next_index
                checked += 1
                continue

            if self.play(track_path, preserve_playlist=True):
                self._playlist_index = next_index
                db.save_playlist_state(index=next_index, active=True)
                logger.info(f"Playing next track: {next_index + 1}/{total_tracks}")
                log_play(
                    "track_start",
                    {"file": track_name, "index": next_index + 1, "total": total_tracks},
                )
                return True

            logger.error(f"Playlist track failed to start, skipping: {track_name}")
            log_error(
                "playlist_track_start_failed",
                {"file": track_name, "index": next_index + 1},
            )
            cursor = next_index
            checked += 1

        self._playlist_active = False
        db.save_playlist_state(active=False)
        logger.error("No playable tracks available in playlist")
        log_error("playlist_no_playable_tracks", {"total_tracks": total_tracks})
        return False

    def stop_playlist(self):
        """Stop the playlist and clear it."""
        self._playlist_active = False
        self._playlist = []
        self._playlist_index = -1
        self.stop()

        # Persist stopped state to database (won't auto-resume on restart)
        db.save_playlist_state(active=False, index=-1)

    def get_playlist_state(self) -> dict:
        """Get current playlist state."""
        return {
            "active": self._playlist_active,
            "tracks": len(self._playlist),
            "current_index": self._playlist_index,
            "loop": self._playlist_loop,
            "current_file": self._playlist[self._playlist_index]
            if 0 <= self._playlist_index < len(self._playlist)
            else None,
        }

    def play(
        self, file_path: str, start_position: float = 0.0, preserve_playlist: bool = False
    ) -> bool:
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

        if not self._ensure_mpg123_compatible(file_path):
            logger.error(f"Audio file is not mpg123-compatible: {file_path}")
            return False

        # Stop any current playback
        if preserve_playlist:
            self._stop_playback_only()
        else:
            self.stop()

        with self._lock:
            self._playback_session += 1
            playback_session = self._playback_session
            try:
                if AUDIO_BACKEND == "mpg123":
                    return self._play_mpg123(file_path, playback_session)
                elif AUDIO_BACKEND == "pygame":
                    return self._play_pygame(file_path, start_position, playback_session)
                else:
                    logger.error("No audio backend available")
                    return False
            except Exception as e:
                logger.error(f"Error playing audio: {e}")
                return False

    def _play_mpg123(self, file_path: str, playback_session: int) -> bool:
        """Play using mpg123 (Pi optimized)."""
        try:
            # Start mpg123 process with FIXED max scale (32768)
            # Volume is controlled SOLELY by ALSA amixer to avoid double-volume curve
            base_args = ["mpg123", "-q", "--scale", "32768"]

            attempts = []
            if platform.system() == "Linux":
                attempts.extend(self._alsa_device_candidates)
            attempts.append(None)  # Final fallback: let mpg123 pick default output

            # Kill any process a concurrent thread may have started and stored
            # before we got the lock.  Without this, that process becomes an
            # orphan: its monitor thread exits on session-mismatch but the
            # subprocess keeps playing until it naturally ends.
            if self._process is not None:
                try:
                    self._process.kill()
                    self._process.wait(timeout=0.5)
                except Exception as e:
                    logger.debug(f"Orphan process kill ignored: {e}")
                finally:
                    self._process = None
            tried_keys = set()

            for device in attempts:
                key = device or "__default__"
                if key in tried_keys:
                    continue
                tried_keys.add(key)

                args = list(base_args)
                if device:
                    args.extend(["-a", device])
                args.append(file_path)

                proc = subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

                # Invalid ALSA device/card usually exits immediately; fallback to next candidate
                time.sleep(0.12)
                if proc.poll() is None:
                    self._process = proc
                    if device:
                        logger.info(f"Using ALSA device: {device}")
                    break

                try:
                    proc.wait(timeout=0.1)
                except Exception:
                    pass

            if self._process is None:
                logger.error("mpg123 could not start with any ALSA device candidate")
                return False

            self.current_file = file_path
            self.is_playing = True
            self._started_at = time.time()  # Record start time
            self.is_paused = False

            logger.info(f"Playing (mpg123): {os.path.basename(file_path)}")

            # Start monitor thread
            self._start_monitor_mpg123(self._process, playback_session)

            return True
        except Exception as e:
            logger.error(f"mpg123 error: {e}")
            return False

    def _play_pygame(
        self, file_path: str, start_position: float, playback_session: int
    ) -> bool:
        """Play using pygame (dev fallback)."""
        import pygame

        if not self._ensure_pygame_mixer():
            logger.error("pygame mixer init failed before playback")
            return False

        if pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()

        def _start_once() -> bool:
            pygame.mixer.music.load(file_path)
            pygame.mixer.music.set_volume(self._volume / 100.0)
            pygame.mixer.music.play(start=start_position)
            # Give pygame a short window to flip into busy state.
            time.sleep(0.08)
            return pygame.mixer.music.get_busy()

        started = _start_once()
        if not started:
            logger.warning(
                "pygame reported not-busy right after play; retrying mixer init once"
            )
            if not self._ensure_pygame_mixer(force_reinit=True):
                logger.error("pygame mixer re-init failed")
                return False
            started = _start_once()
            if not started:
                logger.error(
                    "pygame playback failed to start (no busy state): %s",
                    os.path.basename(file_path),
                )
                return False

        self.current_file = file_path
        self.is_playing = True
        self.is_paused = False
        self._position = start_position

        # Get duration
        try:
            sound = pygame.mixer.Sound(file_path)
            self._duration = sound.get_length()
        except (pygame.error, FileNotFoundError):
            self._duration = 0.0

        self._started_at = time.time()  # Record start time

        logger.info(f"Playing (pygame): {os.path.basename(file_path)}")

        # Start monitor
        self._start_monitor_pygame(playback_session)

        return True

    def _ensure_pygame_mixer(self, force_reinit: bool = False) -> bool:
        """Ensure pygame mixer is initialized and volume is applied."""
        import pygame

        try:
            if force_reinit and pygame.mixer.get_init():
                pygame.mixer.quit()
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.set_volume(self._volume / 100.0)
            return True
        except Exception as e:
            logger.error(f"Failed to initialize pygame mixer: {e}")
            return False

    def _stop_playback_only(self) -> None:
        """Stop playback without touching playlist state."""
        with self._lock:
            self._playback_session += 1
            self.is_playing = False
            self.is_paused = False
            self.current_file = None
            self._position = 0.0
            self._started_at = 0.0
            self._stop_event.set()

        if AUDIO_BACKEND == "mpg123" and self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=0.5)
            except Exception as e:
                logger.debug(f"Process kill ignored: {e}")
            finally:
                self._process = None
        elif AUDIO_BACKEND == "pygame":
            import pygame

            pygame.mixer.music.stop()

    def stop_preview(self, resume_allowed: bool = True) -> bool:
        """Stop preview playback without disabling the playlist."""
        playlist_was_active = self._playlist_active and len(self._playlist) > 0
        playlist_files = list(self._playlist) if playlist_was_active else []
        playlist_index = self._playlist_index
        playlist_loop = self._playlist_loop

        self._stop_playback_only()

        if resume_allowed and playlist_was_active and playlist_files:
            next_idx = (playlist_index + 1) % len(playlist_files)
            self.apply_playlist_state(
                playlist=playlist_files,
                index=next_idx - 1,
                loop=playlist_loop,
                runtime_active=True,
                db_active=True,
                play_next=True,
            )
        return True

    def stop(self) -> bool:
        """Stop playback safely."""
        # 1. Update state FIRST to prevent UI race conditions
        with self._lock:
            was_playing = self.is_playing or bool(self._process)
            self._playback_session += 1
            self.is_playing = False
            self.is_paused = False
            self.current_file = None
            self._position = 0.0
            self._started_at = 0.0  # Reset start time
            self._stop_event.set()
            # CRITICAL: Disable playlist to prevent monitor thread from calling play_next()
            self._playlist_active = False

        # Persist stopped state to database (prevents auto-resume on restart)
        db.save_playlist_state(active=False)

        # 2. Kill process safely
        if AUDIO_BACKEND == "mpg123" and self._process:
            try:
                # Use SIGKILL for immediate termination
                self._process.kill()
                self._process.wait(timeout=0.5)
            except Exception as e:
                logger.debug(f"Process kill ignored: {e}")
            finally:
                self._process = None

        elif AUDIO_BACKEND == "pygame":
            import pygame

            pygame.mixer.music.stop()

        logger.info("Playback stopped")
        if was_playing:
            log_play("stop", {})
        return True

    # Pause/Resume removed for stability with mpg123
    def pause(self) -> bool:
        return False

    def resume(self) -> bool:
        return False

    def set_volume(self, volume: int) -> bool:
        """Set volume level (0-100) using logarithmic mapping for natural feel."""
        volume = max(0, min(100, volume))
        prev_volume = self._volume
        self._volume = volume
        _volume_changed = prev_volume != volume

        if AUDIO_BACKEND == "pygame":
            import pygame

            # Ensure mixer is init
            if not pygame.mixer.get_init():
                try:
                    pygame.mixer.init()
                except Exception as e:
                    logger.debug("pygame mixer init failed: %s", e)
            if pygame.mixer.get_init():
                pygame.mixer.music.set_volume(volume / 100.0)

        # Always try to set hardware volume on Pi (mpg123 or others), but only on Linux
        if platform.system() == "Linux":
            try:
                # Pi4 Calibration Curve
                # Pi4's analog (3.5mm) output has a terrible logarithmic curve:
                #   HW 100% = +4dB, HW 90% = -6dB, HW 80% = -15dB, HW 70% = -25dB (min audible)
                # Solution: UI 0-9% = mute, UI 10-100% → HW 70-100%
                import math

                if volume < 10:
                    # Mute for 0-9%
                    success = self._set_hardware_volume(["mute"])
                    if _volume_changed:
                        logger.info(f"Volume set to: {volume}% (muted, below threshold)")
                        log_volume(
                            "change", {"ui_volume": volume, "hw_volume": 0, "muted": True}
                        )
                else:
                    # Calibration: hw = 70 + sqrt((ui-10)/90) * 30
                    # UI 10% → HW 70%, UI 50% → HW 90%, UI 100% → HW 100%
                    hw_volume = int(round(70 + math.sqrt((volume - 10) / 90.0) * 30))
                    success = self._set_hardware_volume([f"{hw_volume}%", "unmute"])
                    if _volume_changed:
                        logger.info(
                            f"Volume set to: {volume}% (HW calibrated: {hw_volume}%)"
                        )
                        log_volume(
                            "change",
                            {"ui_volume": volume, "hw_volume": hw_volume, "muted": False},
                        )

                if not success:
                    logger.warning("amixer failed for all card/control candidates")
            except (subprocess.SubprocessError, OSError) as e:
                logger.warning(f"Failed to set hardware volume: {e}")

        return True

    def get_volume(self) -> int:
        """Get current volume level."""
        return self._volume

    def get_position(self) -> float:
        """Get current playback position in seconds."""
        if AUDIO_BACKEND == "pygame" and self.is_playing:
            import pygame

            return pygame.mixer.music.get_pos() / 1000.0
        return self._position

    def get_duration(self) -> float:
        """Get duration of current track in seconds."""
        return self._duration

    def get_state(self) -> dict:
        """Get current player state."""
        state = {
            "current_file": self.current_file,
            "filename": os.path.basename(self.current_file)
            if self.current_file
            else None,
            "is_playing": self.is_playing,
            "is_paused": self.is_paused,
            "position": self.get_position(),
            "duration": self._duration,
            "started_at": int(self._started_at * 1000)
            if self._started_at
            else 0,  # Unix ms
            "volume": self._volume,
            "backend": AUDIO_BACKEND,
        }
        # Add playlist info
        state["playlist"] = self.get_playlist_state()
        return state

    def _start_monitor_mpg123(
        self, process: Optional[subprocess.Popen], playback_session: int
    ):
        """Monitor mpg123 process for completion."""
        self._stop_event.clear()
        if process is None:
            return

        def monitor():
            while not self._stop_event.is_set():
                if playback_session != self._playback_session:
                    break

                if process.poll() is not None:
                    if playback_session != self._playback_session:
                        break

                    # Process ended
                    with self._lock:
                        if playback_session != self._playback_session:
                            break
                        self.is_playing = False
                        self.current_file = None
                        if self._process is process:
                            self._process = None

                    logger.info("Track ended (mpg123)")
                    log_play("track_end", {"backend": "mpg123"})

                    if self.on_track_end:
                        self.on_track_end()

                    break
                time.sleep(0.5)

        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()

    def _start_monitor_pygame(self, playback_session: int):
        """Monitor pygame playback for completion."""
        self._stop_event.clear()

        def monitor():
            import pygame

            while not self._stop_event.is_set():
                if playback_session != self._playback_session:
                    break

                if (
                    not pygame.mixer.music.get_busy()
                    and self.is_playing
                    and not self.is_paused
                ):
                    with self._lock:
                        if playback_session != self._playback_session:
                            break
                        self.is_playing = False
                        self.current_file = None
                        self._position = 0.0

                    logger.info("Track ended (pygame)")
                    log_play("track_end", {"backend": "pygame"})

                    if self.on_track_end:
                        self.on_track_end()

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


if __name__ == "__main__":
    # Test the player
    logging.basicConfig(level=logging.INFO)
    print(f"Audio backend: {AUDIO_BACKEND}")
    player = get_player()
    print(f"Volume: {player.get_volume()}%")
    print("State:", player.get_state())
