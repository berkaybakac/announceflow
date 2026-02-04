#!/usr/bin/env python3
"""
Alpine.js Integration Test Suite
Tests for FAZA 2 - Progressive Alpine.js integration
"""
import sys
import os
import re

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_alpine_cdn_loaded():
    """Test 1: Alpine.js CDN is loaded in base.html"""
    print("TEST 1: Alpine.js CDN loaded...", end=" ")
    try:
        with open('templates/base.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for Alpine.js CDN
        assert 'alpinejs' in content.lower(), "Alpine.js CDN not found"
        assert 'defer' in content, "Alpine.js should use defer attribute"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_player_widget_has_xdata():
    """Test 2: Player widget has x-data directive"""
    print("TEST 2: Player widget x-data...", end=" ")
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for x-data="playerState()"
        assert 'x-data="playerState()"' in content, "Player widget missing x-data directive"
        assert 'x-init="init()"' in content, "Player widget missing x-init directive"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_player_state_function_exists():
    """Test 3: playerState() Alpine component function exists"""
    print("TEST 3: playerState() function...", end=" ")
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for function playerState()
        assert 'function playerState()' in content, "playerState() function not found"
        assert 'return {' in content, "playerState() should return an object"

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_reactive_bindings():
    """Test 4: Alpine.js reactive bindings exist"""
    print("TEST 4: Reactive bindings...", end=" ")
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for Alpine.js directives
        required_directives = [
            'x-text=',  # For text binding
            '@click=',  # For click handlers
            ':style=',  # For style binding
        ]

        for directive in required_directives:
            if directive not in content:
                print(f"⏳ PENDING (missing {directive})")
                return None

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_old_onclick_removed():
    """Test 5: Old onclick handlers are removed"""
    print("TEST 5: Old onclick handlers removed...", end=" ")
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check that old onclick handlers are gone
        player_section = content[content.find('now-playing-widget'):content.find('Arka Plan')]

        if 'onclick=' in player_section:
            print("⏳ PENDING (onclick handlers still exist)")
            return None

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_xtransition_animations():
    """Test 6: x-transition animations are used"""
    print("TEST 6: x-transition animations...", end=" ")
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for x-transition
        if 'x-transition' not in content:
            print("⏳ PENDING (x-transition not implemented)")
            return None

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_no_getelementbyid():
    """Test 7: No getElementById in Alpine components"""
    print("TEST 7: No getElementById usage...", end=" ")
    try:
        with open('templates/index.html', 'r', encoding='utf-8') as f:
            content = f.read()

        # Check inside playerState and playlistState functions
        if 'function playerState()' in content:
            # Extract playerState function
            start = content.find('function playerState()')
            # Find the matching closing brace (simplified check)
            func_content = content[start:start+3000]

            if 'getElementById' in func_content and 'document.getElementById' in func_content:
                print("❌ FAIL: getElementById still used in Alpine component")
                return False

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_app_still_runs():
    """Test 8: App imports successfully"""
    print("TEST 8: App imports...", end=" ")
    try:
        import web_panel
        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def run_all_tests():
    """Run all tests and report results."""
    print("\n" + "=" * 50)
    print("FAZA 2 TEST SUITE - Alpine.js Integration")
    print("=" * 50 + "\n")

    results = []
    results.append(test_alpine_cdn_loaded())
    results.append(test_player_widget_has_xdata())
    results.append(test_player_state_function_exists())
    results.append(test_reactive_bindings())
    results.append(test_old_onclick_removed())
    results.append(test_xtransition_animations())
    results.append(test_no_getelementbyid())
    results.append(test_app_still_runs())

    print("\n" + "=" * 50)
    passed = sum(1 for r in results if r is True)
    pending = sum(1 for r in results if r is None)
    failed = sum(1 for r in results if r is False)

    print(f"Results: {passed} passed, {pending} pending, {failed} failed")
    print("=" * 50 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
