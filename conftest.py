import sys
import pathlib

# Ensure src/ is on sys.path so tests can import tap and pick_coords
sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
