"""Lectura de datos públicos de la Cámara Argentina de FCI (estadisticas.cafci.org.ar).

- ficha(fondo, clase): cuotaparte, rendimientos directos (1 mes, 90 días, 12 meses), fecha y patrimonio.
- series(pares): rendimiento acumulado semanal de los últimos 12 meses (comparador, hasta 4 fondos por pedido).
CloudFront a veces responde 403 al primer pedido: se reintenta con espera.
"""
import html, json, re, time, urllib.request

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
BASE = 'https://estadisticas.cafci.org.ar'


def _get(url, tries=4, timeout=40):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8',
                                                       'Accept-Language': 'es-AR,es;q=0.9'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            last = e
            time.sleep(2 + 3 * i)
    raise last


def _num(s):
    return float(s.replace('.', '').replace(',', '.'))


def _text(page):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page, flags=re.S)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    return re.sub(r'\s+', ' ', t)


def ficha(fondo, clase):
    t = _text(_get(f'{BASE}/fondos/{fondo}?clase={clase}'))
    m = re.search(r'Rendimiento Histórico \((Directo|TNA)\) — al (\d\d)/(\d\d)/(\d{4})', t)
    if not m:
        raise ValueError(f'ficha {fondo}/{clase}: sin rendimientos')
    tna = m.group(1) == 'TNA'
    as_of = f'{m.group(4)}-{m.group(3)}-{m.group(2)}'
    seg = t[m.end():m.end() + 700]
    import datetime as dt
    d = dt.date.fromisoformat(as_of)
    prev = (d.replace(day=1) - dt.timedelta(days=1))
    month_days = (d - prev.replace(day=min(d.day, prev.day))).days
    days = {'1 mes': month_days, '90 días': 90, '12 meses': 365}

    def per(label):
        mm = re.search(re.escape(label) + r' (-?[\d.,]+) %', seg)
        if not mm: return None
        v = _num(mm.group(1))
        if tna:  # TNA sobre días corridos -> rendimiento directo del período
            v = v * days[label] / 365
        return round(v, 2) + 0.0

    out = {'asOf': as_of, 'r1m': per('1 mes'), 'r3m': per('90 días'), 'r12m': per('12 meses'), 'tna': tna}
    mv = re.search(r'Valor por cada cuotaparte: ([\d.,]+)', t)
    if mv: out['cuota'] = _num(mv.group(1))
    mp = re.search(r'Patrimonio bajo administración: ([\d.,]+)', t)
    if mp: out['aumClase'] = round(_num(mp.group(1)))
    ml = re.search(r'Plazo de Liquidación (\d+) d', t)
    if ml: out['liq'] = int(ml.group(1))
    return out


def series(pairs):
    """pairs: [(fondo, clase, etiqueta_clase)] (hasta 4). Devuelve {(fondo, clase): [(iso, pct), ...]}."""
    q = ','.join(f'{f}:{c}' for f, c, _ in pairs)
    page = _get(f'{BASE}/comparador-resultado?fondos={q}&periodo=ultimoAnio&visualizacion=grafico')
    m = re.search(r'data-line-chart-data-value="([^"]*)"', page)
    if not m:
        raise ValueError('comparador sin datos')
    data = json.loads(html.unescape(m.group(1)))
    out = {}
    for (f, c, lab), se in zip(pairs, data['series']):
        if lab and lab.lower() not in se['label'].lower():
            raise ValueError(f'serie inesperada para {f}/{c}: {se["label"]}')
        out[(f, c)] = [(p['fecha'], float(p['rendimiento_pct'])) for p in se['points'] if p.get('rendimiento_pct') is not None]
    return out
