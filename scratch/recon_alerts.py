import requests, json
r = requests.get("https://api.alerts.in.ua/v1/alerts/active.json", timeout=15)
print("STATUS", r.status_code)
print(r.text[:500])
