
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
    app.run(host='0.0.0.0', port=port, debug=True)
