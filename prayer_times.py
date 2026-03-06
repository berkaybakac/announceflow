"""
AnnounceFlow - Prayer Times Service
Fetches prayer times from Diyanet API for Turkey cities/districts.
"""
import logging
import json
import os
import tempfile
from datetime import datetime
from typing import Optional, Dict, List, Tuple
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


# Import event logger (lazy to avoid circular imports)
def _log_prayer_event(event: str, data: dict = None):
    try:
        from logger import log_prayer

        log_prayer(event, data)
    except ImportError:
        pass


def _log_error_event(event: str, data: dict = None):
    try:
        from logger import log_error

        log_error(event, data)
    except ImportError:
        pass


# Cache file path
CACHE_FILE = "prayer_times_cache.json"
CACHE_HORIZON_DAYS = 30
MAX_FETCH_DAYS = 30

# Turkey cities and their districts
# Turkey cities cache
CITIES_CACHE_FILE = "cities_districts_cache.json"


def _mark_corrupt_file(path: str, err: Exception) -> None:
    """Move unreadable JSON file aside for forensic inspection."""
    if not os.path.exists(path):
        return

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    corrupt_path = f"{path}.corrupt.{stamp}"
    try:
        os.replace(path, corrupt_path)
        logger.warning("Corrupt cache moved to %s", corrupt_path)
        _log_error_event(
            "prayer_cache_corrupt",
            {"file": path, "corrupt_file": corrupt_path, "error": type(err).__name__},
        )
    except OSError as move_err:
        logger.error("Failed to move corrupt cache file %s: %s", path, move_err)
        _log_error_event(
            "prayer_cache_corrupt",
            {"file": path, "error": f"move_failed:{type(move_err).__name__}"},
        )


def _atomic_write_json(path: str, payload: Dict) -> None:
    """Atomically write JSON content to disk."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp_prayer_", suffix=".json", dir=directory)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)

        # Best effort durability for directory entry updates.
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _load_json_file(path: str, default: Dict) -> Dict:
    """Load JSON from disk and quarantine corrupted files."""
    if not os.path.exists(path):
        return dict(default)

    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else dict(default)
    except json.JSONDecodeError as e:
        _mark_corrupt_file(path, e)
    except OSError:
        pass

    return dict(default)


def _load_geo_cache() -> Dict:
    """Load cached cities and districts."""
    return _load_json_file(
        CITIES_CACHE_FILE,
        {
            "cities": {},
            "districts": {},
        },
    )  # cities: {name: id}, districts: {city_name: [districts]}


def _save_geo_cache(cache: Dict):
    """Save cities and districts to cache."""
    try:
        _atomic_write_json(CITIES_CACHE_FILE, cache)
    except Exception as e:
        logger.error(f"Geo cache save error: {e}")


def get_cities() -> List[str]:
    """Get list of all Turkey cities from API."""
    cache = _load_geo_cache()

    # Return cached cities if available
    if cache["cities"]:
        return sorted(cache["cities"].keys())

    # Fetch from API
    try:
        url = "https://ezanvakti.emushaf.net/sehirler?ulke=2"
        req = urllib.request.Request(url, headers={"User-Agent": "AnnounceFlow/1.0"})

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

            cities = {}
            for item in data:
                # API returns "ADANA", "İSTANBUL" etc - convert to proper title case
                final_name = _turkish_title(item["SehirAdi"])
                cities[final_name] = item["SehirID"]

            cache["cities"] = cities
            _save_geo_cache(cache)
            return sorted(cities.keys())

    except urllib.error.HTTPError as e:
        logger.warning(f"Cities API HTTP error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        if "timed out" in str(e.reason).lower():
            logger.warning(f"Cities API timeout: {e.reason}")
        else:
            logger.warning(f"Cities API network error: {e.reason}")
    except Exception as e:
        logger.error(f"City fetch error: {type(e).__name__}: {e}")

    # Fallback list if API fails
    return [
        "Adana",
        "Ankara",
        "Antalya",
        "Bursa",
        "Diyarbakır",
        "Erzurum",
        "Gaziantep",
        "İstanbul",
        "İzmir",
        "Konya",
        "Trabzon",
        "Van",
    ]


def get_districts(city: str) -> List[str]:
    """Get districts for a city from API."""
    cache = _load_geo_cache()

    # Check cache first
    if city in cache["districts"]:
        return sorted(cache["districts"][city])

    # Need city ID
    city_id = cache["cities"].get(city)

    # If city not in cache (maybe manually typed or cache stale), try to refresh cities
    if not city_id:
        get_cities()  # Refresh cache
        cache = _load_geo_cache()  # Reload
        city_id = cache["cities"].get(city)

    if not city_id:
        # Try case-insensitive lookup
        for c_name, c_id in cache["cities"].items():
            if c_name.lower() == city.lower():
                city_id = c_id
                break

    if not city_id:
        return []

    # Fetch districts from API
    try:
        url = f"https://ezanvakti.emushaf.net/ilceler?sehir={city_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "AnnounceFlow/1.0"})

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

            districts = []
            district_ids = {}  # Store IDs too
            for item in data:
                name = item["IlceAdi"].title()
                districts.append(name)
                district_ids[name] = item["IlceID"]

            # Update cache - store both names and IDs
            cache["districts"][city] = sorted(districts)
            if "district_ids" not in cache:
                cache["district_ids"] = {}
            cache["district_ids"][city] = district_ids
            _save_geo_cache(cache)
            return sorted(districts)

    except urllib.error.HTTPError as e:
        logger.warning(f"Districts API HTTP error for {city}: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        if "timed out" in str(e.reason).lower():
            logger.warning(f"Districts API timeout for {city}: {e.reason}")
        else:
            logger.warning(f"Districts API network error for {city}: {e.reason}")
    except Exception as e:
        logger.error(f"District fetch error for {city}: {type(e).__name__}: {e}")

    return []


def _load_cache() -> Dict:
    """Load cached prayer times."""
    return _load_json_file(CACHE_FILE, {})


def _save_cache(cache: Dict):
    """Save prayer times to cache."""
    try:
        _atomic_write_json(CACHE_FILE, cache)
    except Exception as e:
        logger.error(f"Cache save error: {e}")


def _resolve_cache_key(city: str, district: str, date_key: str) -> str:
    return f"{city}_{district}_{date_key}"


def _parse_date_key(date_key: str) -> Optional[datetime]:
    try:
        return datetime.strptime(date_key, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _prune_cache_for_city_district(
    cache: Dict, city: str, district: str, horizon_days: int
) -> bool:
    prefix = f"{city}_{district}_"
    dated_entries = []
    for key in list(cache.keys()):
        if not key.startswith(prefix):
            continue
        date_key = key[len(prefix) :]
        parsed = _parse_date_key(date_key)
        if parsed is None:
            continue
        dated_entries.append((parsed, key))

    if len(dated_entries) <= horizon_days:
        return False

    dated_entries.sort(key=lambda item: item[0], reverse=True)
    removed = False
    for _, key in dated_entries[horizon_days:]:
        if key in cache:
            cache.pop(key, None)
            removed = True
    return removed


def _find_stale_cached_times(
    cache: Dict, city: str, district: str
) -> Optional[Tuple[str, Dict]]:
    prefix = f"{city}_{district}_"
    stale_candidates = []
    for key, value in cache.items():
        if not key.startswith(prefix):
            continue
        if not isinstance(value, dict):
            continue
        date_key = key[len(prefix) :]
        stale_candidates.append((date_key, value))

    if not stale_candidates:
        return None

    stale_candidates.sort(key=lambda item: item[0], reverse=True)
    return stale_candidates[0]


def get_prayer_times(
    city: str, district: str, allow_network: bool = True
) -> Tuple[Optional[Dict], str]:
    """Resolve prayer times with optional network access.

    Returns:
        (times, source) where source is one of:
        - cache_fresh
        - cache_stale
        - network
        - none
    """
    district = district or "Merkez"
    today = datetime.now().strftime("%Y-%m-%d")
    today_dt = _parse_date_key(today)
    cache_key = _resolve_cache_key(city, district, today)

    cache = _load_cache()
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        logger.debug(f"Using cached prayer times for {city}/{district} ({today})")
        return cached, "cache_fresh"

    if allow_network and fetch_weekly_prayer_times(city, district):
        refreshed = _load_cache()
        cached = refreshed.get(cache_key)
        if isinstance(cached, dict):
            return cached, "network"
        cache = refreshed

    if allow_network:
        # Fallback: Try single-day fetch from alternative API
        try:
            url = f"https://api.collectapi.com/pray/all?data.city={city}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "AnnounceFlow/1.0",
                    "content-type": "application/json",
                },
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))

                if data.get("success") and data.get("result"):
                    result = (
                        data["result"][0]
                        if isinstance(data["result"], list)
                        else data["result"]
                    )

                    prayer_times = {
                        "imsak": result.get("imsak", ""),
                        "gunes": result.get("gunes", ""),
                        "ogle": result.get("ogle", ""),
                        "ikindi": result.get("ikindi", ""),
                        "aksam": result.get("aksam", ""),
                        "yatsi": result.get("yatsi", ""),
                        "date": today,
                    }

                    cache[cache_key] = prayer_times
                    _save_cache(cache)

                    logger.info(
                        f"Fetched prayer times (alt API) for {city}: {prayer_times}"
                    )
                    return prayer_times, "network"

        except urllib.error.HTTPError as e:
            logger.warning(f"Alternative API HTTP error for {city}: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            if "timed out" in str(e.reason).lower():
                logger.warning(f"Alternative API timeout for {city}: {e.reason}")
            else:
                logger.warning(f"Alternative API network error for {city}: {e.reason}")
        except Exception as e:
            logger.error(f"Alternative API error for {city}: {type(e).__name__}: {e}")

    stale_entry = _find_stale_cached_times(cache, city, district)
    if stale_entry:
        stale_date_key, stale = stale_entry
        stale_dt = _parse_date_key(stale_date_key)
        if today_dt and stale_dt:
            age_days = (today_dt.date() - stale_dt.date()).days
            if 0 <= age_days <= CACHE_HORIZON_DAYS:
                logger.warning(
                    "Using stale cache for %s/%s - no fresh data available (age=%s days)",
                    city,
                    district,
                    age_days,
                )
                return stale, "cache_stale"
            logger.warning(
                "Rejecting stale cache for %s/%s - out of horizon (age=%s days, horizon=%s)",
                city,
                district,
                age_days,
                CACHE_HORIZON_DAYS,
            )
        else:
            logger.warning(
                "Rejecting stale cache for %s/%s - invalid stale date key: %s",
                city,
                district,
                stale_date_key,
            )

    return None, "none"


def _normalize_turkish(text: str) -> str:
    """Normalize Turkish characters for comparison."""
    replacements = {
        "İ": "I",
        "ı": "i",
        "Ğ": "G",
        "ğ": "g",
        "Ü": "U",
        "ü": "u",
        "Ş": "S",
        "ş": "s",
        "Ö": "O",
        "ö": "o",
        "Ç": "C",
        "ç": "c",
        "i̇": "i",
        "İ": "I",  # Combining dot variants
    }
    for tr, en in replacements.items():
        text = text.replace(tr, en)
    return text.lower()


def _turkish_title(text: str) -> str:
    """Convert text to title case with proper Turkish character handling.

    Standard Python title() doesn't handle Turkish I/İ correctly.
    """
    if not text:
        return text

    # First lowercase with Turkish rules (İ→i, I→ı)
    lower_map = str.maketrans("İIĞÜŞÖÇ", "iığüşöç")
    lowered = text.translate(lower_map).lower()

    # Then titlecase each word with Turkish rules
    words = lowered.split()
    result = []
    for word in words:
        if not word:
            continue
        first = word[0]
        # Turkish uppercase: i→İ, ı→I
        if first == "i":
            first = "İ"
        elif first == "ı":
            first = "I"
        else:
            first = first.upper()
        result.append(first + word[1:])

    return " ".join(result)


def _get_district_id(city: str, district: str) -> Optional[str]:
    """Get district ID for API calls."""
    cache = _load_geo_cache()

    # Ensure districts are loaded
    if city not in cache.get("districts", {}):
        get_districts(city)
        cache = _load_geo_cache()

    # Get district ID
    district_ids = cache.get("district_ids", {}).get(city, {})
    district_id = district_ids.get(district)

    # Try normalized Turkish comparison
    if not district_id:
        district_norm = _normalize_turkish(district)
        for name, did in district_ids.items():
            if _normalize_turkish(name) == district_norm:
                return did

    return district_id


def fetch_weekly_prayer_times(city: str, district: str) -> bool:
    """
    Fetch prayer times and cache them with bounded horizon.
    This protects against internet outages during prayer times.

    Returns True if successful, False otherwise.
    """
    cache = _load_cache()

    # Get district ID for API
    district_id = _get_district_id(city, district)
    if not district_id:
        logger.warning(f"Could not find district ID for {city}/{district}")
        return False

    try:
        # Ezan Vakti API returns multiple days - use district ID
        url = f"https://ezanvakti.emushaf.net/vakitler?ilce={district_id}"

        req = urllib.request.Request(url, headers={"User-Agent": "AnnounceFlow/1.0"})

        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

            if data and len(data) > 0:
                cached_count = 0
                for day_data in data[:MAX_FETCH_DAYS]:
                    # Parse the date from API response
                    date_str = day_data.get("MiladiTarihKisa", "")
                    if not date_str:
                        continue

                    # Convert DD.MM.YYYY to YYYY-MM-DD
                    try:
                        parts = date_str.split(".")
                        if len(parts) == 3:
                            date_key = f"{parts[2]}-{parts[1]}-{parts[0]}"
                        else:
                            continue
                    except Exception:
                        continue

                    cache_key = f"{city}_{district}_{date_key}"

                    prayer_times = {
                        "imsak": day_data.get("Imsak", ""),
                        "gunes": day_data.get("Gunes", ""),
                        "ogle": day_data.get("Ogle", ""),
                        "ikindi": day_data.get("Ikindi", ""),
                        "aksam": day_data.get("Aksam", ""),
                        "yatsi": day_data.get("Yatsi", ""),
                        "date": date_key,
                    }

                    cache[cache_key] = prayer_times
                    cached_count += 1

                if cached_count > 0:
                    _prune_cache_for_city_district(
                        cache, city, district, CACHE_HORIZON_DAYS
                    )
                    _save_cache(cache)
                    logger.info(
                        f"Cached {cached_count} days of prayer times for {city}/{district}"
                    )
                    _log_prayer_event(
                        "fetch",
                        {
                            "city": city,
                            "district": district,
                            "days": cached_count,
                            "horizon_days": CACHE_HORIZON_DAYS,
                        },
                    )
                    return True

    except urllib.error.HTTPError as e:
        logger.warning(
            f"Prayer API HTTP error for {city}/{district}: {e.code} {e.reason}"
        )
    except urllib.error.URLError as e:
        if "timed out" in str(e.reason).lower():
            logger.warning(f"Prayer API timeout for {city}/{district}: {e.reason}")
        else:
            logger.warning(
                f"Prayer API network error for {city}/{district}: {e.reason}"
            )
    except Exception as e:
        logger.error(
            f"Prayer times fetch error for {city}/{district}: {type(e).__name__}: {e}"
        )

    return False


def fetch_prayer_times(city: str, district: str) -> Optional[Dict]:
    """
    Fetch today's prayer times from cache or API.
    Uses bounded horizon caching for resilience against internet outages.

    Returns dict with keys: imsak, gunes, ogle, ikindi, aksam, yatsi
    """
    times, _ = get_prayer_times(city, district, allow_network=True)
    return times


def is_prayer_time(city: str, district: str, buffer_minutes: int = 1) -> bool:
    """
    Check if current time is within a prayer time window.

    Args:
        city: City name
        district: District name
        buffer_minutes: Minutes before prayer to silence, and after to resume

    Returns:
        True if we should be silent (in prayer time window)
    """
    if not city:
        return False

    times = fetch_prayer_times(city, district or "Merkez")
    if not times:
        return False

    now = datetime.now()
    current_minutes = now.hour * 60 + now.minute

    # Check each prayer time
    for prayer_key in ["imsak", "gunes", "ogle", "ikindi", "aksam", "yatsi"]:
        prayer_time_str = times.get(prayer_key, "")
        if not prayer_time_str:
            continue

        try:
            # Parse HH:MM
            h, m = map(int, prayer_time_str.split(":"))
            prayer_minutes = h * 60 + m

            # Check if within buffer window
            start = prayer_minutes - buffer_minutes
            # Ezan typically lasts ~5 minutes, add buffer after
            end = prayer_minutes + 5 + buffer_minutes

            if start <= current_minutes <= end:
                logger.info(f"In prayer time window: {prayer_key} ({prayer_time_str})")
                _log_prayer_event(
                    "in_window", {"prayer": prayer_key, "time": prayer_time_str}
                )
                return True

        except (ValueError, AttributeError):
            continue

    return False


def get_next_prayer_time(city: str, district: str) -> Optional[Dict]:
    """Get the next upcoming prayer time."""
    times = fetch_prayer_times(city, district or "Merkez")
    if not times:
        return None

    now = datetime.now()
    current_minutes = now.hour * 60 + now.minute

    prayer_names = {
        "imsak": "İmsak",
        "gunes": "Güneş",
        "ogle": "Öğle",
        "ikindi": "İkindi",
        "aksam": "Akşam",
        "yatsi": "Yatsı",
    }

    for prayer_key in ["imsak", "gunes", "ogle", "ikindi", "aksam", "yatsi"]:
        prayer_time_str = times.get(prayer_key, "")
        if not prayer_time_str:
            continue

        try:
            h, m = map(int, prayer_time_str.split(":"))
            prayer_minutes = h * 60 + m

            if prayer_minutes > current_minutes:
                return {
                    "name": prayer_names.get(prayer_key, prayer_key),
                    "time": prayer_time_str,
                }
        except ValueError:
            continue

    # All prayers passed for today, return tomorrow's imsak
    return {"name": "İmsak (yarın)", "time": times.get("imsak", "--:--")}


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.DEBUG)

    print("Cities:", get_cities()[:5], "...")
    print("Istanbul districts:", get_districts("İstanbul")[:5], "...")

    times = fetch_prayer_times("İstanbul", "Kadıköy")
    print("Prayer times:", times)

    print("Is prayer time now:", is_prayer_time("İstanbul", "Kadıköy"))
    print("Next prayer:", get_next_prayer_time("İstanbul", "Kadıköy"))
