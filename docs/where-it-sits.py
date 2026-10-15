# Draws docs/where-it-sits-{light,dark}.svg in Kept's colours: python3 docs/where-it-sits.py (run from docs/).
# Writes where-it-sits-{light,dark}.svg: Kept, the integration inside Home Assistant, and what it feeds.
THEMES = {
  'light': dict(paper='#F2F1EC', surface='#FBFAF7', sunken='#ECEAE4', line='#DEDBD3', ink='#1C1B19', ink2='#55524C', ink3='#6B675F', amber='#F0B03A', amberink='#2E2100', amtext='#8A5700'),
  'dark':  dict(paper='#151412', surface='#1E1C19', sunken='#26231F', line='#34302A', ink='#F2EFE9', ink2='#BDB7AC', ink3='#9A948A', amber='#F0B03A', amberink='#2E2100', amtext='#F4C165'),
}
SANS = "'IBM Plex Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace"
def svg(t):
  c = THEMES[t]
  def text(x, y, s, size=15, weight=400, fill=c['ink'], font=SANS, anchor='start'):
    return f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{s}</text>'
  def box(x, y, w, h, stroke=c['line'], fill=c['surface'], sw=1, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'
  def arrow(x1, y1, x2, y2, col=c['ink3']):
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="2" marker-end="url(#a-{t})"/>'
  p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="360" viewBox="0 0 960 360" role="img" aria-label="Kept, the Kept integration inside Home Assistant, and the dashboards, automations and calendar it feeds">',
       f'<defs><marker id="a-{t}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{c["ink3"]}"/></marker></defs>',
       f'<rect width="960" height="360" rx="18" fill="{c["paper"]}"/>']
  # Kept
  p += [box(24, 104, 228, 162),
        f'<rect x="44" y="124" width="30" height="30" rx="6" fill="{c["amber"]}"/><circle cx="51" cy="131" r="2.4" fill="{c["paper"]}"/>',
        text(59, 146, 'K', 17, 700, c['amberink'], SANS, 'middle'),
        text(86, 146, 'Kept', 20, 700),
        text(44, 184, 'Your server', 14, 500, c['ink2']),
        text(44, 212, 'What needs you, per location', 14, 400, c['ink2']),
        text(44, 236, 'Vehicles · due dates', 14, 400, c['ink2'])]
  # Home Assistant frame
  p += [box(372, 20, 564, 320, c['line'], c['sunken'], 1, '6 6'),
        text(396, 52, 'Home Assistant', 18, 700),
        text(538, 52, 'only if you already run it', 13, 400, c['ink3'])]
  # The integration
  p += [box(400, 104, 236, 162, c['amber'], c['surface'], 2),
        text(420, 136, 'Kept integration', 17, 700),
        text(420, 158, 'this repository · HACS', 12, 500, c['amtext'], MONO),
        text(420, 192, 'Sensors per location', 14, 400, c['ink2']),
        text(420, 216, 'Vehicles · a calendar', 14, 400, c['ink2']),
        text(420, 240, 'Two actions', 14, 400, c['ink2'])]
  # What it feeds
  for i, (y, s) in enumerate([(104, 'Dashboards'), (161, 'Automations · alerts'), (218, 'Calendar')]):
    p += [box(684, y, 228, 48), text(702, y + 30, s, 15, 600), arrow(636, 185, 680, y + 24)]
  # Kept ⇄ integration
  p += [arrow(254, 150, 396, 150), text(312, 140, 'every 5 min', 12, 500, c['ink3'], MONO, 'middle'),
        arrow(396, 224, 256, 224), text(312, 246, 'readings back', 12, 500, c['ink3'], MONO, 'middle')]
  p += [text(24, 300, 'Token: read-only for sensors,', 12, 500, c['ink3'], MONO), text(24, 320, 'read + change for the actions', 12, 500, c['ink3'], MONO)]
  p.append('</svg>')
  return '\n'.join(p)
for t in THEMES: open(f'where-it-sits-{t}.svg', 'w').write(svg(t))
