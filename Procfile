release: python scripts/release.py
web: gunicorn wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120 --preload --access-logfile -
