#!/bin/bash
set -e
cd /home/shayan/Desktop/Web/Safferon-Felahati
.venv/bin/python wsgi.py > /tmp/flask.log 2>&1 &
FLASK_PID=$!
sleep 2

echo "=== Testing all routes ==="
for path in "/" "/about" "/cart" "/register" "/login" "/profile" "/admin/login"; do
    status=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000$path)
    # Check for csrf_token in POST forms
    csrf=$(curl -s http://127.0.0.1:5000$path | grep -o "csrf_token" | wc -l)
    echo "$path -> $status (csrf tokens: $csrf)"
done

# Test 404
status=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/nonexistent)
echo "/nonexistent -> $status"

# Test POST with CSRF on register
token=$(curl -s http://127.0.0.1:5000/register | grep -oP 'name="csrf_token" value="\K[^"]+')
echo "CSRF token from register: ${token:0:20}..."

if [ -n "$token" ]; then
    status=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:5000/register \
        -d "username=testuser123&password=testpass&password_confirm=testpass&full_name=test&phone=09123456789&address=test&csrf_token=$token")
    echo "POST /register -> $status"
fi

kill $FLASK_PID 2>/dev/null
echo "=== Done ==="
