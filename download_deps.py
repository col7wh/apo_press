# download_deps.py
import urllib.request

URLS = [
    ('https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js', 'static/js/chart.umd.min.js'),
    ('https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.0/dist/chartjs-plugin-zoom.min.js', 'static/js/chartjs-plugin-zoom.min.js'),
    ('https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js', 'static/js/chartjs-adapter-date-fns.bundle.min.js')
]

for url, path in URLS:
    print(f"Скачиваю {url} -> {path}")
    try:
        urllib.request.urlretrieve(url, path)
        print("✅ OK")
    except Exception as e:
        print(f"❌ Ошибка: {e}")