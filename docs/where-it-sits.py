# Draws docs/where-it-sits-{light,dark}.svg in Kept's colours: python3 docs/where-it-sits.py (run from docs/).
# Draws where-it-sits-{light,dark}.svg in Kept's colours (apps/web/src/styles/tokens.css): Kept, the
# integration inside Home Assistant, and what it feeds. Run: python3 where-it-sits.py (from docs/).
T = {
  'light': dict(paper='#F2F1EC', surface='#FBFAF7', sunken='#ECEAE4', line='#DEDBD3', ink='#1C1B19', ink2='#55524C', ink3='#6B675F',
                amber='#F0B03A', amberink='#2E2100', amtext='#8A5700', danger='#B42318', warn='#A6480A', info='#2459A8', ok='#1E7B3C'),
  'dark':  dict(paper='#151412', surface='#1E1C19', sunken='#26231F', line='#34302A', ink='#F2EFE9', ink2='#BDB7AC', ink3='#9A948A',
                amber='#F0B03A', amberink='#2E2100', amtext='#F4C165', danger='#F97066', warn='#F28A4E', info='#7FA8EE', ok='#5CC27F'),
}
SANS = "'IBM Plex Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace"

def svg(t):
  c = T[t]
  def tx(x, y, s, size=14, w=400, fill=c['ink'], font=SANS, anchor='start'):
    return f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{w}" fill="{fill}" text-anchor="{anchor}">{s}</text>'
  def card(x, y, w, h, stroke=c['line'], sw=1, fill=c['surface'], r=14):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
  def pill(cx, cy, s, w):
    return (f'<rect x="{cx - w/2}" y="{cy - 12}" width="{w}" height="24" rx="12" fill="{c["surface"]}" stroke="{c["line"]}"/>'
            + tx(cx, cy + 4.5, s, 12, 500, c['ink2'], MONO, 'middle'))
  def line(x1, y1, x2, y2):
    return f'<path d="M{x1} {y1}H{x2}" stroke="{c["ink3"]}" stroke-width="1.75" fill="none" marker-end="url(#a{t})"/>'
  def curve(x1, y1, x2, y2):
    m = (x1 + x2) / 2
    return f'<path d="M{x1} {y1}C{m} {y1},{m} {y2},{x2} {y2}" stroke="{c["ink3"]}" stroke-width="1.75" fill="none" marker-end="url(#a{t})"/>'
  ic = c['ink2']
  icons = {
    'grid': f'<g fill="none" stroke="{ic}" stroke-width="1.8"><rect x="0" y="0" width="8" height="8" rx="2"/><rect x="12" y="0" width="8" height="8" rx="2"/><rect x="0" y="12" width="8" height="8" rx="2"/><rect x="12" y="12" width="8" height="8" rx="2"/></g>',
    'bolt': f'<path d="M11 0L2 12h7l-2 8 9-12H9z" fill="none" stroke="{ic}" stroke-width="1.8" stroke-linejoin="round"/>',
    'cal':  f'<g fill="none" stroke="{ic}" stroke-width="1.8"><rect x="0" y="3" width="20" height="17" rx="3"/><path d="M0 9h20M6 0v5M14 0v5"/></g>',
  }
  p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="960" height="340" viewBox="0 0 960 340" role="img" aria-label="Kept on your server; the Kept integration inside Home Assistant reads it every 5 minutes and sends readings back; it feeds dashboards, automations and the calendar">',
       f'<defs><marker id="a{t}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L9 5L0 9z" fill="{c["ink3"]}"/></marker></defs>',
       f'<rect width="960" height="340" rx="20" fill="{c["paper"]}"/>']
  # Kept
  p += [card(24, 92, 236, 196),
        f'<rect x="44" y="112" width="32" height="32" rx="7" fill="{c["amber"]}"/><circle cx="51.5" cy="119.5" r="2.6" fill="{c["surface"]}"/>',
        tx(60, 135, 'K', 18, 700, c['amberink'], SANS, 'middle'), tx(88, 135, 'Kept', 21, 700),
        tx(44, 170, 'Your server', 13, 500, c['ink3'])]
  for i, (n, label, col) in enumerate([('2', 'Overdue', c['danger']), ('1', 'Due', c['amber']), ('1', 'Expiring', c['warn']), ('1', 'Lent out', c['info'])]):
    y = 196 + i * 24
    p += [f'<circle cx="50" cy="{y - 4}" r="4.5" fill="{col}"/>', tx(64, y, label, 14, 500, c['ink2']), tx(236, y, n, 15, 700, c['ink'], SANS, 'end')]
  # Home Assistant frame
  p += [card(364, 16, 572, 300, c['line'], 1, c['sunken'], 20),
        tx(388, 50, 'Home Assistant', 17, 700),
        f'<rect x="522" y="34" width="150" height="22" rx="11" fill="none" stroke="{c["line"]}"/>', tx(597, 49.5, 'only if you run it', 12, 500, c['ink3'], SANS, 'middle')]
  # The integration
  p += [card(392, 92, 252, 196, c['amber'], 2),
        tx(412, 124, 'Kept integration', 17, 700), tx(412, 146, 'this repository · HACS', 12, 500, c['amtext'], MONO)]
  for i, (eid, val) in enumerate([('sensor.home_overdue', '2'), ('sensor.home_due', '1'), ('calendar.home_due_dates', ''), ('kept.log_reading', '')]):
    y = 184 + i * 26
    p += [f'<path d="M412 {y - 17}H624" stroke="{c["line"]}"/>', tx(412, y, eid, 12, 500, c['ink2'], MONO)]
    if val: p.append(tx(624, y, val, 14, 700, c['ink'], SANS, 'end'))
  # What it feeds
  for i, (icon, label) in enumerate([('grid', 'Dashboards'), ('bolt', 'Automations · alerts'), ('cal', 'Calendar')]):
    y = 100 + i * 64; cy = y + 26
    p += [card(692, y, 220, 52, r=12), f'<g transform="translate(712 {cy - 10})">{icons[icon]}</g>', tx(746, cy + 5, label, 15, 600),
          curve(644, 190, 688, cy)]
  # Kept ⇄ integration
  p += [line(262, 160, 388, 160), pill(325, 136, 'every 5 min', 104),
        line(390, 226, 264, 226), pill(325, 250, 'readings back', 116)]
  p += [tx(24, 302, 'Token: read-only for the sensors,', 12, 500, c['ink3'], MONO), tx(24, 320, 'read + change for the actions', 12, 500, c['ink3'], MONO)]
  p.append('</svg>')
  return '\n'.join(p)

for t in T: open(f'where-it-sits-{t}.svg', 'w').write(svg(t))
