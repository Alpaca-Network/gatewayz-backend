import sys
import os

# Set PYTHONPATH
sys.path.append(os.path.join(os.getcwd(), "src"))

try:
    print("Attempting to import src.main...")
    import src.main
    print("Successfully imported src.main!")
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Startup Exception: {e}")
    sys.exit(1)
