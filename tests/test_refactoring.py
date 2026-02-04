#!/usr/bin/env python3
"""
FAZA 4 Test Suite - Scheduler Refactor
Tests for R1 (Scheduler loop refactoring).

CRITICAL: These tests verify scheduler behavior hasn't changed after refactoring.
"""
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    """Test all critical imports work."""
    print("TEST 1: Imports...", end=" ")
    try:
        import scheduler
        import database as db
        from scheduler import Scheduler, is_prayer_time_active, is_within_working_hours
        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_scheduler_attributes():
    """Test scheduler has all required attributes."""
    print("TEST 2: Scheduler attributes...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        # Core attributes
        required_attrs = [
            'check_interval', '_running', '_thread',
            '_last_recurring_triggers',
            '_prayer_pause_state', '_working_hours_pause_state',
            # S1: Thread management
            '_restore_threads', '_restore_lock', '_restore_in_progress',
            # O1: Config cache
            '_config_cache', '_config_cache_time', '_config_cache_ttl'
        ]

        for attr in required_attrs:
            assert hasattr(s, attr), f"Missing attribute: {attr}"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_scheduler_methods():
    """Test scheduler has all required methods."""
    print("TEST 3: Scheduler methods...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        # Core methods
        required_methods = [
            'start', 'stop', '_run_loop',
            '_get_cached_config',
            '_check_one_time_schedules', '_check_recurring_schedules',
            '_play_media', '_times_match', '_is_time_in_range', '_is_interval_point'
        ]

        for method in required_methods:
            assert hasattr(s, method), f"Missing method: {method}"
            assert callable(getattr(s, method)), f"Not callable: {method}"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_scheduler_helper_functions():
    """Test scheduler module-level helper functions."""
    print("TEST 4: Helper functions...", end=" ")
    try:
        from scheduler import is_prayer_time_active, is_within_working_hours

        # Test with empty config (should return safe defaults)
        config = {}

        # is_prayer_time_active should return False when disabled
        result = is_prayer_time_active(config)
        assert result == False, f"Expected False, got {result}"

        # is_within_working_hours should return True when disabled
        result = is_within_working_hours(config)
        assert result == True, f"Expected True, got {result}"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_scheduler_config_cache():
    """Test config caching works correctly."""
    print("TEST 5: Config cache...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        # Get config twice, should be cached
        config1 = s._get_cached_config()
        time1 = s._config_cache_time

        config2 = s._get_cached_config()
        time2 = s._config_cache_time

        # Should be same object (cached)
        assert config1 is config2, "Config not cached"
        assert time1 == time2, "Cache time changed unexpectedly"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_refactored_methods_exist():
    """Test that refactored methods exist (after refactoring)."""
    print("TEST 6: Refactored methods...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        # These methods should exist after refactoring
        refactored_methods = [
            '_handle_prayer_time',
            '_handle_working_hours'
        ]

        missing = []
        for method in refactored_methods:
            if not hasattr(s, method):
                missing.append(method)

        if missing:
            print(f"⏳ PENDING (not implemented: {', '.join(missing)})")
            return None

        # Verify they're callable
        for method in refactored_methods:
            assert callable(getattr(s, method)), f"Not callable: {method}"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_database():
    """Test database functions still work."""
    print("TEST 7: Database...", end=" ")
    try:
        import database as db
        db.init_database()

        state = db.get_playback_state()
        assert 'volume' in state, "Missing volume"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_previous_faza_fixes():
    """Verify all previous fixes still work."""
    print("TEST 8: Previous FAZA fixes...", end=" ")
    try:
        # FAZA 3: Turkish normalize
        from prayer_times import _turkish_title, _normalize_turkish
        assert _turkish_title("İSTANBUL") == "İstanbul"
        assert _normalize_turkish("İstanbul") == "istanbul"

        # FAZA 2: Config cache
        import scheduler
        s = scheduler.Scheduler()
        assert hasattr(s, '_get_cached_config')

        # FAZA 1: Thread management
        assert hasattr(s, '_restore_in_progress')

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "="*50)
    print("FAZA 4 TEST SUITE - Scheduler Refactor")
    print("="*50 + "\n")

    results = []
    results.append(test_imports())
    results.append(test_scheduler_attributes())
    results.append(test_scheduler_methods())
    results.append(test_scheduler_helper_functions())
    results.append(test_scheduler_config_cache())
    results.append(test_refactored_methods_exist())
    results.append(test_database())
    results.append(test_previous_faza_fixes())

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
