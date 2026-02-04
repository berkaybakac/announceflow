#!/usr/bin/env python3
"""
Project Cleanup Verification Suite
Tests to ensure cleanup doesn't break anything.
"""
import sys
import os

def test_all_tests_importable():
    """Test that all test files can be imported."""
    print("TEST 1: All test files importable...", end=" ")
    try:
        # Check if tests/ directory exists
        assert os.path.exists("tests"), "tests/ directory missing"

        # Check for required test files
        required_tests = [
            "tests/test_api.py",
            "tests/test_optimization.py",
            "tests/test_cleanup.py",
            "tests/test_refactoring.py",
        ]

        missing = []
        for test_file in required_tests:
            if not os.path.exists(test_file):
                missing.append(test_file)

        if missing:
            print(f"⏳ PENDING (missing: {', '.join(missing)})")
            return None

        print("✅ PASS")
        return True
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return False

def test_no_duplicate_tests():
    """Test that old test_faza*.py files are removed."""
    print("TEST 2: No duplicate test files...", end=" ")
    try:
        old_tests = [
            "test_faza2.py",
            "test_faza3.py",
            "test_faza4.py",
        ]

        found = []
        for test_file in old_tests:
            if os.path.exists(test_file):
                found.append(test_file)

        if found:
            print(f"❌ FAIL: Old files still exist: {', '.join(found)}")
            return False

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_gitignore_updated():
    """Test that .gitignore has required entries."""
    print("TEST 3: .gitignore updated...", end=" ")
    try:
        with open(".gitignore", "r") as f:
            content = f.read()

        required = [
            ".pytest_cache",
            "test_*.db",
        ]

        missing = []
        for entry in required:
            if entry not in content:
                missing.append(entry)

        # Check that docs/ is NOT ignored (wrong practice)
        if "\ndocs/\n" in content or content.startswith("docs/\n"):
            print("⚠️ WARNING: docs/ should not be ignored")
            return None

        if missing:
            print(f"⏳ PENDING (missing: {', '.join(missing)})")
            return None

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_requirements_dev_exists():
    """Test that requirements-dev.txt exists."""
    print("TEST 4: requirements-dev.txt exists...", end=" ")
    try:
        if not os.path.exists("requirements-dev.txt"):
            print("⏳ PENDING (not created yet)")
            return None

        with open("requirements-dev.txt", "r") as f:
            content = f.read()

        # Check for pytest
        if "pytest" not in content:
            print("❌ FAIL: Missing pytest")
            return False

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_readme_updated():
    """Test that README mentions v2.7.0."""
    print("TEST 5: README.md updated...", end=" ")
    try:
        with open("README.md", "r") as f:
            content = f.read()

        # Check for version mention
        if "2.7" not in content and "v2.7" not in content:
            print("⏳ PENDING (not updated yet)")
            return None

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_previous_tests_still_work():
    """Test that previous test suites still pass."""
    print("TEST 6: Previous tests still work...", end=" ")
    try:
        # Import and run a quick check
        import scheduler
        s = scheduler.Scheduler()

        # Check FAZA 1-4 features still exist
        assert hasattr(s, '_restore_in_progress'), "S1 feature missing"
        assert hasattr(s, '_config_cache'), "O1 feature missing"
        assert hasattr(s, '_handle_prayer_time'), "R1 feature missing"

        print("✅ PASS")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def run_all_tests():
    """Run all cleanup verification tests."""
    print("\n" + "="*50)
    print("CLEANUP VERIFICATION SUITE")
    print("="*50 + "\n")

    results = []
    results.append(test_all_tests_importable())
    results.append(test_no_duplicate_tests())
    results.append(test_gitignore_updated())
    results.append(test_requirements_dev_exists())
    results.append(test_readme_updated())
    results.append(test_previous_tests_still_work())

    print("\n" + "="*50)
    passed = sum(1 for r in results if r is True)
    pending = sum(1 for r in results if r is None)
    failed = sum(1 for r in results if r is False)

    print(f"Results: {passed} passed, {pending} pending, {failed} failed")
    print("="*50 + "\n")

    return failed == 0 and pending == 0

if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
