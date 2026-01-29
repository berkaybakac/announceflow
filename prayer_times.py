"""
AnnounceFlow - Prayer Times Service
Fetches prayer times from Diyanet API for Turkey cities/districts.
"""
import logging
import json
import os
from datetime import datetime
from typing import Optional, Dict, List
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Cache file path
CACHE_FILE = 'prayer_times_cache.json'

# Turkey cities and their districts
# Turkey cities cache
CITIES_CACHE_FILE = 'cities_districts_cache.json'

def _load_geo_cache() -> Dict:
    """Load cached cities and districts."""
    if os.path.exists(CITIES_CACHE_FILE):
        try:
            with open(CITIES_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"cities": {}, "districts": {}}  # cities: {name: id}, districts: {city_name: [districts]}

def _save_geo_cache(cache: Dict):
    """Save cities and districts to cache."""
    try:
        with open(CITIES_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
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
        req = urllib.request.Request(url, headers={'User-Agent': 'AnnounceFlow/1.0'})
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            cities = {}
            for item in data:
                # Store as {Name: ID}
                # API returns "ADANA", normalize title case for display
                final_name = item['SehirAdi'].title()
                # Fix problematic I/İ in title
                if final_name.startswith('I'):
                    final_name = 'I' + final_name[1:]
                if 'İ' in item['SehirAdi']:
                    final_name = item['SehirAdi'].replace('İ', 'i').title()
                
                # Just use the raw name from mapping for ID lookup, return sorted keys
                cities[final_name] = item['SehirID']
            
            cache["cities"] = cities
            _save_geo_cache(cache)
            return sorted(cities.keys())
            
    except Exception as e:
        logger.error(f"City fetch error: {e}")
        # Fallback list if API fails
        return ["Adana", "Ankara", "Antalya", "Bursa", "Diyarbakır", "Erzurum", "Gaziantep", "İstanbul", "İzmir", "Konya", "Trabzon", "Van"]

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
        get_cities() # Refresh cache
        cache = _load_geo_cache() # Reload
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
        req = urllib.request.Request(url, headers={'User-Agent': 'AnnounceFlow/1.0'})
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            districts = []
            for item in data:
                districts.append(item['IlceAdi'].title())
            
            # Update cache
            cache["districts"][city] = sorted(districts)
            _save_geo_cache(cache)
            return sorted(districts)
            
    except Exception as e:
        logger.error(f"District fetch error for {city}: {e}")
        return []

def _load_cache() -> Dict:
    """Load cached prayer times."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_cache(cache: Dict):
    """Save prayer times to cache."""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Cache save error: {e}")


def fetch_prayer_times(city: str, district: str) -> Optional[Dict]:
    """
    Fetch today's prayer times from API.
    Uses Ezan Vakti API (emushaf.net) for Diyanet data.
    
    Returns dict with keys: imsak, gunes, ogle, ikindi, aksam, yatsi
    """
    today = datetime.now().strftime('%Y-%m-%d')
    cache_key = f"{city}_{district}_{today}"
    
    # Check cache first
    cache = _load_cache()
    if cache_key in cache:
        logger.debug(f"Using cached prayer times for {city}/{district}")
        return cache[cache_key]
    
    # Try to fetch from API
    try:
        # Using Ezan Vakti API
        url = f"https://ezanvakti.emushaf.net/vakitler?il={city}&ilce={district}"
        
        req = urllib.request.Request(url, headers={
            'User-Agent': 'AnnounceFlow/1.0'
        })
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if data and len(data) > 0:
                # Get today's times
                today_data = data[0]
                
                prayer_times = {
                    'imsak': today_data.get('Imsak', ''),
                    'gunes': today_data.get('Gunes', ''),
                    'ogle': today_data.get('Ogle', ''),
                    'ikindi': today_data.get('Ikindi', ''),
                    'aksam': today_data.get('Aksam', ''),
                    'yatsi': today_data.get('Yatsi', ''),
                    'date': today
                }
                
                # Cache the result
                cache[cache_key] = prayer_times
                _save_cache(cache)
                
                logger.info(f"Fetched prayer times for {city}/{district}: {prayer_times}")
                return prayer_times
                
    except urllib.error.URLError as e:
        logger.error(f"Prayer times API error: {e}")
    except Exception as e:
        logger.error(f"Prayer times fetch error: {e}")
    
    # Fallback: Try alternative API
    try:
        url = f"https://api.collectapi.com/pray/all?data.city={city}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'AnnounceFlow/1.0',
            'content-type': 'application/json'
        })
        
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if data.get('success') and data.get('result'):
                result = data['result'][0] if isinstance(data['result'], list) else data['result']
                
                prayer_times = {
                    'imsak': result.get('imsak', ''),
                    'gunes': result.get('gunes', ''),
                    'ogle': result.get('ogle', ''),
                    'ikindi': result.get('ikindi', ''),
                    'aksam': result.get('aksam', ''),
                    'yatsi': result.get('yatsi', ''),
                    'date': today
                }
                
                cache[cache_key] = prayer_times
                _save_cache(cache)
                
                logger.info(f"Fetched prayer times (alt API) for {city}: {prayer_times}")
                return prayer_times
                
    except Exception as e:
        logger.error(f"Alternative API error: {e}")
    
    return None


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
    for prayer_key in ['imsak', 'gunes', 'ogle', 'ikindi', 'aksam', 'yatsi']:
        prayer_time_str = times.get(prayer_key, '')
        if not prayer_time_str:
            continue
            
        try:
            # Parse HH:MM
            h, m = map(int, prayer_time_str.split(':'))
            prayer_minutes = h * 60 + m
            
            # Check if within buffer window
            start = prayer_minutes - buffer_minutes
            # Ezan typically lasts ~5 minutes, add buffer after
            end = prayer_minutes + 5 + buffer_minutes
            
            if start <= current_minutes <= end:
                logger.info(f"In prayer time window: {prayer_key} ({prayer_time_str})")
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
        'imsak': 'İmsak',
        'gunes': 'Güneş',
        'ogle': 'Öğle',
        'ikindi': 'İkindi',
        'aksam': 'Akşam',
        'yatsi': 'Yatsı'
    }
    
    for prayer_key in ['imsak', 'gunes', 'ogle', 'ikindi', 'aksam', 'yatsi']:
        prayer_time_str = times.get(prayer_key, '')
        if not prayer_time_str:
            continue
            
        try:
            h, m = map(int, prayer_time_str.split(':'))
            prayer_minutes = h * 60 + m
            
            if prayer_minutes > current_minutes:
                return {
                    'name': prayer_names.get(prayer_key, prayer_key),
                    'time': prayer_time_str
                }
        except ValueError:
            continue
    
    # All prayers passed for today, return tomorrow's imsak
    return {
        'name': 'İmsak (yarın)',
        'time': times.get('imsak', '--:--')
    }


if __name__ == '__main__':
    # Test
    logging.basicConfig(level=logging.DEBUG)
    
    print("Cities:", get_cities()[:5], "...")
    print("Istanbul districts:", get_districts("İstanbul")[:5], "...")
    
    times = fetch_prayer_times("İstanbul", "Kadıköy")
    print("Prayer times:", times)
    
    print("Is prayer time now:", is_prayer_time("İstanbul", "Kadıköy"))
    print("Next prayer:", get_next_prayer_time("İstanbul", "Kadıköy"))
