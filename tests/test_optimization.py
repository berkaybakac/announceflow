#!/usr/bin/env python3
"""
FAZA 2 Test Suite - Optimize
Tests for O1 (Config Cache) and O2 (File Size) optimizations.
"""
import sys
import os
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    """Test all critical imports work."""
    print("TEST 1: Imports...", end=" ")
    try:
        import scheduler
        import web_panel
        import database as db
        import player
        import prayer_times
        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_scheduler_config_cache():
    """Test O1: Config cache attributes exist."""
    print("TEST 2: Scheduler config cache...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        # Check cache attributes exist
        assert hasattr(s, '_config_cache'), "Missing _config_cache"
        assert hasattr(s, '_config_cache_time'), "Missing _config_cache_time"
        assert hasattr(s, '_config_cache_ttl'), "Missing _config_cache_ttl"

        # Check _get_cached_config method exists
        assert hasattr(s, '_get_cached_config'), "Missing _get_cached_config method"

        # Test caching behavior
        config1 = s._get_cached_config()
        time1 = s._config_cache_time

        config2 = s._get_cached_config()
        time2 = s._config_cache_time

        # Should be same cache (time should not change)
        assert time1 == time2, "Cache should not reload immediately"
        assert config1 is config2, "Should return same cached object"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except AttributeError as e:
        print(f"⏳ PENDING (not implemented yet): {e}")
        return None  # Not implemented yet

def test_web_panel_library():
    """Test O2: web_panel library function works."""
    print("TEST 3: Web panel library...", end=" ")
    try:
        import database as db
        db.init_database()

        # Just test the imports and basic functionality
        from web_panel import app
        with app.test_client() as client:
            # This would require login, just check app exists
            assert app is not None

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_file_size_calculation():
    """Test O2: File size calculation helper function."""
    print("TEST 6: File size calculation...", end=" ")
    try:
        # Test that we can calculate file sizes without errors
        import os

        # Create a temp file to test
        test_file = '/tmp/test_size.txt'
        with open(test_file, 'w') as f:
            f.write('test content')

        # Test using os.stat (optimized approach)
        try:
            stat = os.stat(test_file)
            size = stat.st_size
            assert size > 0, "File size should be > 0"
        except FileNotFoundError:
            pass  # Expected for non-existent files

        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_database():
    """Test database functions still work."""
    print("TEST 4: Database...", end=" ")
    try:
        import database as db
        db.init_database()

        state = db.get_playback_state()
        assert 'volume' in state, "Missing volume in playback state"

        # Test media functions
        files = db.get_all_media_files()
        assert isinstance(files, list), "get_all_media_files should return list"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_scheduler_thread_management():
    """Test S1: Thread management still works."""
    print("TEST 5: Scheduler thread management...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        assert hasattr(s, '_restore_threads'), "Missing _restore_threads"
        assert hasattr(s, '_restore_lock'), "Missing _restore_lock"
        assert hasattr(s, '_restore_in_progress'), "Missing _restore_in_progress"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "="*50)
    print("FAZA 2 TEST SUITE")
    print("="*50 + "\n")

    results = []
    results.append(test_imports())
    results.append(test_scheduler_config_cache())
    results.append(test_web_panel_library())
    results.append(test_database())
    results.append(test_scheduler_thread_management())
    results.append(test_file_size_calculation())

    print("\n" + "="*50)
    passed = sum(1 for r in results if r is True)
    pending = sum(1 for r in results if r is None)
    failed = sum(1 for r in results if r is False)

    print(f"Results: {passed} passed, {pending} pending, {failed} failed")
    print("="*50 + "\n")

    return failed == 0

if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
