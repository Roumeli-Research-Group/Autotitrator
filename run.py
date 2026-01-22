
import sys
import os

# Add src to python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from src.app import app

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
