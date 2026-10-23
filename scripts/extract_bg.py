import re

with open('scratch_razorpay.html', 'r', encoding='utf-8', errors='ignore') as f:
    html = f.read()

scripts = re.findall(r'href="([^"]+\.js)"|src="([^"]+\.js)"', html)
all_scripts = [s[0] or s[1] for s in scripts]
for s in all_scripts:
    print(s)

# Also look for json data or window.__INITIAL_STATE__ or similar
window_data = re.findall(r'window\.__[A-Za-z0-9_]+__\s*=\s*([^\n;]+)', html)
print(f"Window data items: {len(window_data)}")
