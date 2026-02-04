#!/usr/bin/env python3
"""
FAZA 3 Test Suite - Cleanup
Tests for C1 (Turkish character normalization).
"""
import sys

def test_imports():
    """Test all critical imports work."""
    print("TEST 1: Imports...", end=" ")
    try:
        import prayer_times
        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_normalize_turkish():
    """Test C1: Turkish character normalization."""
    print("TEST 2: Turkish normalize...", end=" ")
    try:
        from prayer_times import _normalize_turkish

        # Test cases for Turkish characters
        test_cases = [
            ("İstanbul", "istanbul"),
            ("ANKARA", "ankara"),
            ("Diyarbakır", "diyarbakir"),
            ("Şanlıurfa", "sanliurfa"),
            ("Ağrı", "agri"),
            ("Muğla", "mugla"),
            ("Çanakkale", "canakkale"),
            ("Üsküdar", "uskudar"),
            ("ÖZEL", "ozel"),
        ]

        for input_text, expected in test_cases:
            result = _normalize_turkish(input_text)
            assert result == expected, f"Expected '{expected}' for '{input_text}', got '{result}'"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_city_name_processing():
    """Test that city names are processed correctly from API format."""
    print("TEST 3: City name processing...", end=" ")
    try:
        # Simulate API data format and test title casing
        api_names = ["İSTANBUL", "ANKARA", "DİYARBAKIR", "ŞANLIURFA", "AĞRI"]
        expected = ["İstanbul", "Ankara", "Diyarbakır", "Şanlıurfa", "Ağrı"]

        # We'll test the helper function once it exists
        # For now, just verify the logic works
        from prayer_times import _turkish_title

        for api_name, exp in zip(api_names, expected):
            result = _turkish_title(api_name)
            assert result == exp, f"Expected '{exp}' for '{api_name}', got '{result}'"

        print("✅ PASS")
        return True
    except AttributeError:
        print("⏳ PENDING (_turkish_title not implemented yet)")
        return None
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_previous_fixes():
    """Verify previous stabilization fixes still work."""
    print("TEST 4: Previous fixes...", end=" ")
    try:
        import scheduler
        s = scheduler.Scheduler()

        # S1: Thread management
        assert hasattr(s, '_restore_threads'), "Missing thread tracking"
        assert hasattr(s, '_restore_in_progress'), "Missing idempotency flag"

        # O1: Config cache
        assert hasattr(s, '_config_cache'), "Missing config cache"
        assert hasattr(s, '_get_cached_config'), "Missing cache method"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "="*50)
    print("FAZA 3 TEST SUITE")
    print("="*50 + "\n")

    results = []
    results.append(test_imports())
    results.append(test_normalize_turkish())
    results.append(test_city_name_processing())
    results.append(test_previous_fixes())

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
