import re

def patch_main_py():
    with open('main.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # Add imports
    if 'from auth import router as auth_router, get_current_user' not in content:
        import_stmt = "from auth import router as auth_router, get_current_user\nfrom models import User\nfrom fastapi import Depends\n"
        content = content.replace("from fastapi import FastAPI, HTTPException, BackgroundTasks, Query", 
                                  import_stmt + "from fastapi import FastAPI, HTTPException, BackgroundTasks, Query")
        
    # Include router
    if 'app.include_router(auth_router)' not in content:
        content = content.replace("app.mount(\"/static\", StaticFiles(directory=\"static\"), name=\"static\")",
                                  "app.mount(\"/static\", StaticFiles(directory=\"static\"), name=\"static\")\napp.include_router(auth_router)")

    # Inject current_user dependency into route functions
    def repl_func(match):
        decorator = match.group(1)
        func_def = match.group(2)
        func_name = match.group(3)
        args = match.group(4)
        
        # If already injected, skip
        if 'current_user' in args:
            return match.group(0)
            
        # Add current_user to args
        if args.strip() == '':
            new_args = 'current_user: User = Depends(get_current_user)'
        else:
            new_args = args + ', current_user: User = Depends(get_current_user)'
            
        return f"{decorator}\n{func_def}{func_name}({new_args})"

    # Regex to find @app.xyz \n def function_name(args):
    # It handles multiline args too
    pattern = r'(@app\.(?:get|post|put|delete)\([^)]+\))\n(async\s+def\s+|def\s+)([a-zA-Z0-9_]+)\((.*?)\)(?:\s*->\s*[^:]+)?:'
    
    new_content = re.sub(pattern, repl_func, content, flags=re.DOTALL)
    
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
        
    print("Patched main.py with current_user dependency!")

if __name__ == "__main__":
    patch_main_py()
