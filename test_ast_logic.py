import os
import sys

# 1. Test ast_evaluator.py Sandbox
sys.path.append(r"c:\Dbro\PallagJoel\Github\Tőzsde figyelő")
from ast_evaluator import safe_eval

def run_tests():
    print("Running Tests for AST Sandbox...")
    passed = 0
    failed = 0

    # Test 1: Simple math
    try:
        res = safe_eval("rf + beta * erp", {"rf": 0.05, "beta": 1.2, "erp": 0.06})
        assert abs(res - (0.05 + 1.2 * 0.06)) < 1e-6
        print("Test 1 (Math): PASS")
        passed += 1
    except Exception as e:
        print(f"Test 1 (Math): FAIL - {e}")
        failed += 1

    # Test 2: Safe functions
    try:
        res = safe_eval("max(a, sum(b, c)) + abs(d)", {"a": 10, "b": 2, "c": 3, "d": -5})
        assert res == 15 # max(10, 5) + 5 = 15
        print("Test 2 (Safe Functions): PASS")
        passed += 1
    except Exception as e:
        print(f"Test 2 (Safe Functions): FAIL - {e}")
        failed += 1

    # Test 3: Code Injection
    try:
        safe_eval("__import__('os').system('echo pwned')", {})
        print("Test 3 (Code Injection): FAIL - Allowed malicious code!")
        failed += 1
    except ValueError as e:
        print(f"Test 3 (Code Injection): PASS - Blocked ({e})")
        passed += 1
    except Exception as e:
        print(f"Test 3 (Code Injection): PASS - Blocked with different exception ({e})")
        passed += 1

    print(f"\nResults: {passed} passed, {failed} failed.")

if __name__ == '__main__':
    run_tests()
