import ast
import operator

class SafeMathEvaluator(ast.NodeVisitor):
    # Engedélyezett matematikai műveletek
    allowed_ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg
    }
    
    def __init__(self, variables: dict):
        self.variables = variables

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op_type = type(node.op)
        if op_type not in self.allowed_ops:
            raise ValueError(f"Unsupported operator: {op_type}")
        return self.allowed_ops[op_type](left, right)

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        op_type = type(node.op)
        if op_type not in self.allowed_ops:
            raise ValueError(f"Unsupported unary operator: {op_type}")
        return self.allowed_ops[op_type](operand)

    def visit_Name(self, node):
        if node.id in self.variables:
            return self.variables[node.id]
        raise NameError(f"Variable '{node.id}' not defined in sandbox.")

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only basic functions are allowed.")
            
        func_name = node.func.id
        args = [self.visit(arg) for arg in node.args]
        
        if func_name == "max":
            if not args: raise ValueError("max() requires arguments")
            return max(args)
        elif func_name == "min":
            if not args: raise ValueError("min() requires arguments")
            return min(args)
        elif func_name == "abs":
            if len(args) != 1: raise ValueError("abs() takes exactly 1 argument")
            return abs(args[0])
        elif func_name == "sum":
            return sum(args)
        else:
            raise ValueError(f"Function '{func_name}' is not allowed.")

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("Only numeric constants are allowed.")

    def generic_visit(self, node):
        # Ha olyan szintaxist találunk ami nincs definiálva, dobjuk.
        raise ValueError(f"Unsupported syntax: {type(node).__name__}")

def safe_eval(expr: str, variables: dict) -> float:
    """
    Safely parses and evaluates a math formula string using AST.
    Usage: safe_eval("revenue * margin", {"revenue": 100, "margin": 0.2})
    """
    try:
        tree = ast.parse(expr, mode='eval')
        evaluator = SafeMathEvaluator(variables)
        return float(evaluator.visit(tree.body))
    except SyntaxError:
        raise ValueError("Syntax error in mathematical formula.")
