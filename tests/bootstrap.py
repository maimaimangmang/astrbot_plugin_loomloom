"""Allow tests to run with the AstrBot plugin files at repository root."""
import sys
import types
from pathlib import Path
root = Path(__file__).resolve().parents[1]
if 'astrbot_plugin_loomloom' not in sys.modules:
    package = types.ModuleType('astrbot_plugin_loomloom')
    package.__path__ = [str(root)]
    sys.modules['astrbot_plugin_loomloom'] = package
