#!/bin/bash
echo '>>> Installing dependencies from requirements.txt...'
wine python -m pip install -r /app/mt5docker/requirements.txt --quiet

echo '>>> environment ready.'
tail -f /dev/null
