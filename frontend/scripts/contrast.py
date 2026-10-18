"""Check text/background contrast for both themes, read straight from src/index.css. Usage: python3 scripts/contrast.py"""
import re, sys
from pathlib import Path

css = (Path(__file__).parent.parent / 'src/index.css').read_text()
def block(sel):
    m = re.search(re.escape(sel) + r'\s*\{(.*?)\n\}', css, re.S)
    return dict(re.findall(r'--([\w-]+):\s*([^;]+);', m.group(1)))

def rgba(v):
    v = v.strip()
    if v.startswith('#'):
        h = v[1:]
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    r, g, b, a = [float(x) for x in re.findall(r'[\d.]+', v)]
    return (r, g, b, a)

def over(fg, bg):  # alpha-composite fg onto an opaque bg
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)

def lum(c):
    ch = [x / 255 for x in c[:3]]
    ch = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)

# (text token, background token, layered-on token or None)
PAIRS = [(t, b, None) for t in ('text', 'text-2', 'muted') for b in ('bg', 'surface', 'surface-2', 'surface-3')]
PAIRS += [('primary', b, None) for b in ('bg', 'surface', 'surface-2')]
PAIRS += [('primary-ink', 'primary', None), ('code-text', 'code-bg', None), ('muted', 'code-bg', None)]
PAIRS += [('primary', 'primary-soft', 'surface'), ('primary', 'primary-soft', 'surface-2'), ('text', 'primary-soft', 'surface')]
PAIRS += [(c, f'{c}-soft', 'surface') for c in ('success', 'warning', 'error')]
PAIRS += [(c, f'{c}-soft', 'surface-2') for c in ('success', 'warning', 'error')]
PAIRS += [('text', 'error-soft', 'surface'), ('text-2', 'warning-soft', 'surface'), ('text-2', 'primary-soft', 'surface'), ('primary', 'primary-soft', 'bg')]
PAIRS += [(a, b, None) for a in ('agent-c', 'agent-i', 'agent-h') for b in ('surface', 'surface-2')]

fails = 0
for theme, sel in (('dark', ":root, :root[data-theme='dark']"), ('light', ":root[data-theme='light']")):
    t = block(sel)
    worst = (99, '')
    for fg, bg, base in PAIRS:
        b = rgba(t[bg])
        if base: b = over(b, rgba(t[base]))
        r = ratio(over(rgba(t[fg]), b), b)
        name = f'{fg} on {bg}' + (f' over {base}' if base else '')
        if r < 4.5: fails += 1; print(f'FAIL {theme:5} {r:5.2f}  {name}')
        worst = min(worst, (r, name))
    print(f'{theme:5}: {len(PAIRS)} pairs, lowest {worst[0]:.2f} ({worst[1]})')
sys.exit(1 if fails else 0)
