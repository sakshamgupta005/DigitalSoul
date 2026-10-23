import urllib.request
import re

req = urllib.request.Request('https://hitodama.in/', headers={'User-Agent': 'Mozilla/5.0'})
html = urllib.request.urlopen(req).read().decode('utf-8', errors='ignore')

css_files = re.findall(r'href="(/_next/static/css/[^"]+\.css)"', html)
print('CSS files:', css_files)

all_css = ""
for cf in css_files:
    css_url = 'https://hitodama.in' + cf
    try:
        css = urllib.request.urlopen(urllib.request.Request(css_url, headers={'User-Agent': 'Mozilla/5.0'})).read().decode('utf-8', errors='ignore')
        all_css += "\n" + css
    except Exception as e:
        print(f"Error fetching {cf}: {e}")

fonts = set(re.findall(r'font-family:[^;}]+', all_css))
print("\nFonts:")
for f in fonts:
    print(f)

vars_found = set(re.findall(r'--[a-zA-Z0-9_-]+:[^;]+', all_css))
print(f"\nTotal CSS variables found: {len(vars_found)}")
for v in sorted(vars_found):
    if any(k in v for k in ['bg', 'color', 'background', 'paper', 'cream', 'font', 'ink', 'text', 'brand', 'solar', 'sand']):
        print(v)

body_rules = re.findall(r'body\s*\{[^}]+\}', all_css)
print("\nBody rules:")
for b in body_rules:
    print(b)
