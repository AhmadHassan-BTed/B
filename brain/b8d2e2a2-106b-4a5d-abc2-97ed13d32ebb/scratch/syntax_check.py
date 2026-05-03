import os
import py_compile
import sys

root = "t:/B"
success = True

for dirpath, dirnames, filenames in os.walk(root):
    if ".git" in dirnames:
        dirnames.remove(".git")
    if "__pycache__" in dirnames:
        dirnames.remove("__pycache__")
    
    for filename in filenames:
        if filename.endswith(".py"):
            path = os.path.join(dirpath, filename)
            try:
                py_compile.compile(path, doraise=True)
                # print(f"OK: {path}")
            except py_compile.PyCompileError as e:
                print(f"ERROR: {path}\n{e}")
                success = False

if not success:
    sys.exit(1)
else:
    print("All real python files compiled successfully.")
