
import sys
import os

# Load .env from the project root (e.g. TITRATOR_ENV=PROD on the instrument)
# BEFORE importing the app, so hardware initialization sees it.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment variables

# Add src to python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.app import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Debug mode (auto-reloader) stat-scans the whole project tree every
    # second - a significant, constant CPU/IO load on a Raspberry Pi SD card.
    # Only enable it in development.
    debug = os.environ.get('TITRATOR_ENV', 'DEV') != 'PROD'
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
