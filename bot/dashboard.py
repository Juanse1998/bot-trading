"""Genera un dashboard HTML estatico a partir del estado del paper trading.

Sin servidor, sin dependencias de JS externas (GitHub Pages sirve el archivo tal
cual). El grafico es un SVG dibujado a mano: menos cosas que se puedan romper.

    python -m bot.dashboard --config config_final.yaml --out docs/index.html
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from bot.contracts import spec


def _svg_curva(curve: list[dict], inicial: float, w: int = 900, h: int = 260) -> str:
    if len(curve) < 2:
        return ('<div class="vacio">Todavía no hay suficientes datos para la curva. '
                'Aparece cuando el bot lleve unas horas corriendo.</div>')

    vals = [c["equity"] for c in curve]
    lo, hi = min(min(vals), inicial), max(max(vals), inicial)
    rango = (hi - lo) or 1
    pad = rango * 0.12
    lo, hi = lo - pad, hi + pad
    rango = hi - lo

    def x(i: int) -> float:
        return 50 + i * (w - 70) / max(len(vals) - 1, 1)

    def y(v: float) -> float:
        return 20 + (hi - v) / rango * (h - 55)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    area = f"50,{y(lo):.1f} {pts} {x(len(vals) - 1):.1f},{y(lo):.1f}"
    y0 = y(inicial)
    up = vals[-1] >= inicial
    color = "#16a34a" if up else "#dc2626"

    ticks = "".join(
        f'<line x1="46" y1="{y(lo + rango * f):.1f}" x2="{w - 15}" y2="{y(lo + rango * f):.1f}"/>'
        f'<text x="42" y="{y(lo + rango * f) + 4:.1f}" text-anchor="end">'
        f'{lo + rango * f:,.0f}</text>'
        for f in (0.1, 0.35, 0.6, 0.85)
    )

    return f"""<svg viewBox="0 0 {w} {h}" class="chart" preserveAspectRatio="xMidYMid meet">
  <g class="grid">{ticks}</g>
  <line class="base" x1="50" y1="{y0:.1f}" x2="{w - 15}" y2="{y0:.1f}"/>
  <text class="baselbl" x="{w - 15}" y="{y0 - 6:.1f}" text-anchor="end">inicial {inicial:,.0f}</text>
  <polygon points="{area}" fill="{color}" opacity="0.10"/>
  <polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"
            stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{x(len(vals) - 1):.1f}" cy="{y(vals[-1]):.1f}" r="4.5" fill="{color}"/>
</svg>"""


def build(cfg_path: str, out: str) -> None:
    cfg = yaml.safe_load(open(cfg_path))
    state_file = Path(cfg["paper"]["state_file"])
    inicial = float(cfg["risk"]["initial_equity"])

    if state_file.exists():
        st = json.loads(state_file.read_text())
    else:
        st = {"equity": inicial, "positions": {}, "history": [], "curve": []}

    eq = float(st["equity"])
    hist = st.get("history", [])
    pos = st.get("positions", {})
    curve = st.get("curve", [])

    pnl_total = eq - inicial
    ret = pnl_total / inicial * 100
    ganadoras = [t for t in hist if t.get("pnl", 0) > 0]
    wr = len(ganadoras) / len(hist) * 100 if hist else 0

    # Drawdown maximo sobre la curva registrada.
    max_dd, pico = 0.0, inicial
    for c in curve:
        pico = max(pico, c["equity"])
        max_dd = min(max_dd, c["equity"] / pico - 1)

    def tarjeta(lbl: str, val: str, cls: str = "") -> str:
        return f'<div class="card"><div class="lbl">{lbl}</div><div class="val {cls}">{val}</div></div>'

    sig = "pos" if pnl_total >= 0 else "neg"
    cards = "".join([
        tarjeta("Equity actual", f"${eq:,.2f}", sig),
        tarjeta("Resultado", f"{pnl_total:+,.2f} USD", sig),
        tarjeta("Retorno", f"{ret:+.2f}%", sig),
        tarjeta("Operaciones", f"{len(hist)}"),
        tarjeta("Aciertos", f"{wr:.0f}%" if hist else "—"),
        tarjeta("Peor caída", f"{max_dd * 100:.1f}%" if curve else "—"),
    ])

    if pos:
        filas = "".join(
            f"<tr><td><b>{spec(s)['nombre']}</b></td><td>{p['qty']:,.4f}</td>"
            f"<td>{p['entry']:,.4f}</td><td class='neg'>{p['stop_loss']:,.4f}</td>"
            f"<td class='pos'>{p['take_profit']:,.4f}</td>"
            f"<td class='muted'>{p['opened_at'][:16].replace('T', ' ')}</td></tr>"
            for s, p in pos.items()
        )
        abiertas = f"""<table>
<thead><tr><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Stop</th>
<th>Objetivo</th><th>Abierta desde</th></tr></thead><tbody>{filas}</tbody></table>"""
    else:
        abiertas = '<div class="vacio">Sin posiciones abiertas.</div>'

    if hist:
        filas = "".join(
            f"<tr><td class='muted'>{t.get('closed_at', '')[:16].replace('T', ' ')}</td>"
            f"<td><b>{spec(t['symbol'])['nombre']}</b></td>"
            f"<td>{t['entry']:,.4f}</td><td>{t.get('exit', 0):,.4f}</td>"
            f"<td class='{'pos' if t.get('pnl', 0) > 0 else 'neg'}'><b>{t.get('pnl', 0):+,.2f}</b></td>"
            f"<td class='muted'>{t.get('reason', '')}</td></tr>"
            for t in reversed(hist[-40:])
        )
        tabla = f"""<table>
<thead><tr><th>Cerrada</th><th>Instrumento</th><th>Entrada</th><th>Salida</th>
<th>P&amp;L</th><th>Motivo</th></tr></thead><tbody>{filas}</tbody></table>"""
    else:
        tabla = ('<div class="vacio">Todavía no cerró ninguna operación. '
                 'La estrategia hace ~15 al mes, así que tené paciencia.</div>')

    html = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot de Trading — Paper</title>
<style>
  :root {{
    --bg:#f6f7f9; --fg:#111827; --muted:#6b7280; --card:#fff; --line:#e5e7eb;
    --pos:#16a34a; --neg:#dc2626;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e; --card:#161b22; --line:#30363d; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 2px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:22px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
    gap:12px; margin-bottom:24px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }}
  .lbl {{ color:var(--muted); font-size:12px; text-transform:uppercase;
    letter-spacing:.04em; margin-bottom:6px; }}
  .val {{ font-size:21px; font-weight:650; font-variant-numeric:tabular-nums; }}
  .pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
  .muted {{ color:var(--muted); }}
  h2 {{ font-size:15px; margin:26px 0 10px; }}
  .panel {{ background:var(--card); border:1px solid var(--line);
    border-radius:10px; padding:16px; overflow-x:auto; }}
  .chart {{ width:100%; height:auto; }}
  .chart .grid line {{ stroke:var(--line); stroke-width:1; }}
  .chart .grid text {{ fill:var(--muted); font-size:11px; }}
  .chart .base {{ stroke:var(--muted); stroke-width:1; stroke-dasharray:4 4; opacity:.6; }}
  .chart .baselbl {{ fill:var(--muted); font-size:11px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px;
    font-variant-numeric:tabular-nums; }}
  th {{ text-align:left; color:var(--muted); font-weight:500; font-size:12px;
    text-transform:uppercase; letter-spacing:.04em; padding:0 10px 8px 0; }}
  td {{ padding:9px 10px 9px 0; border-top:1px solid var(--line); }}
  .vacio {{ color:var(--muted); padding:14px 0; font-size:14px; }}
  footer {{ margin-top:28px; color:var(--muted); font-size:12px; }}
</style></head><body><div class="wrap">
  <h1>Bot de Trading — Paper Trading</h1>
  <div class="sub">
    {len(cfg['symbols'])} instrumentos · velas de {cfg['timeframe']} ·
    {cfg['strategy']['name']} · riesgo {cfg['risk']['risk_per_trade'] * 100:g}% por operación ·
    capital inicial ${inicial:,.0f}
  </div>

  <div class="cards">{cards}</div>

  <h2>Curva de equity</h2>
  <div class="panel">{_svg_curva(curve, inicial)}</div>

  <h2>Posiciones abiertas</h2>
  <div class="panel">{abiertas}</div>

  <h2>Últimas operaciones cerradas</h2>
  <div class="panel">{tabla}</div>

  <footer>
    Actualizado: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ·
    Dinero simulado. Esto mide si el edge sobrevive fuera de muestra.
  </footer>
</div></body></html>"""

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html)
    print(f"dashboard -> {out}  (equity {eq:,.2f}, {len(hist)} operaciones)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config_final.yaml")
    p.add_argument("--out", default="docs/index.html")
    build(p.parse_args().config, p.parse_args().out)


if __name__ == "__main__":
    main()
