from ast_evaluator import safe_eval

def test_formulas():
    vars = {"a": 10, "b": 20, "c": 30}
    try:
        print("max:", safe_eval("max(a, b)", vars))
    except Exception as e:
        print("max failed:", type(e).__name__, "-", e)

    try:
        print("min:", safe_eval("min(a, b)", vars))
    except Exception as e:
        print("min failed:", type(e).__name__, "-", e)

    try:
        print("abs:", safe_eval("abs(-a)", vars))
    except Exception as e:
        print("abs failed:", type(e).__name__, "-", e)

    try:
        print("sum:", safe_eval("sum(a, b, c)", vars))
    except Exception as e:
        print("sum failed:", type(e).__name__, "-", e)

if __name__ == "__main__":
    test_formulas()
