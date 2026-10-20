#!/usr/bin/env python3
"""Genera data/data.json para el Monitor FCI (sitio público).

Fuente única: CAFCI (estadisticas.cafci.org.ar).
- Elige todos los fondos vigentes de Balanz, Santander, Macro, Galicia y BBVA.
  Dólares: money market y renta fija. Pesos: todos los tipos (los dollar linked van en la familia 'dl', que se ve en la solapa Dólares).
- Solo fondos con Clase A (personas humanas) con inversores; se muestra siempre la Clase A.
- Rendimientos 1 mes / 90 días / 12 meses de la ficha oficial (money market: TNA -> rendimiento directo).
- Peor caída, volatilidad (desvío semanal x raíz de 52) y gráfico, de la serie semanal de 12 meses del comparador.

Uso: python3 scripts/build.py [salida=data/data.json]
"""
import datetime as dt, io, json, math, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cafci_src  # noqa: E402

UA = cafci_src.UA
BANKS = [('balanz', re.compile(r'^Balanz', re.I)), ('santander', re.compile(r'^Santander', re.I)),
         ('macro', re.compile(r'^Macro Fondos', re.I)), ('galicia', re.compile(r'^Galicia', re.I)),
         ('bbva', re.compile(r'^BBVA', re.I))]
EXCLUDE_CLASS = re.compile(r'Ley N|Decreto|ex Letes', re.I)
INST_LBL = {'balanz': 'Balanz', 'santander': 'Santander', 'macro': 'Macro', 'galicia': 'Galicia', 'bbva': 'BBVA'}
DL_RX = re.compile(r'dollar[\s-]*linked|d[oó]lar[\s-]*linked|ajuste al d[oó]lar|evoluci[oó]n del (tipo de cambio|d[oó]lar oficial)', re.I)
MIN_AUM = {'USD': 50_000, 'ARS': 50_000_000}   # por debajo, fondo sin actividad
TZ = dt.timezone(dt.timedelta(hours=-3))


def get_bytes(url, headers=None, tries=4, timeout=90):
    last = None
    for i in range(tries):
        try:
            h = {'User-Agent': UA, 'Accept': '*/*'}
            if headers: h.update(headers)
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e; time.sleep(3 + 4 * i)
    raise last


def load_sources():
    cat = json.loads(get_bytes('https://estadisticas.cafci.org.ar/consulta-de-fondos.json', {'Accept': 'application/json'}))
    raw = get_bytes('https://api.pub.cafci.org.ar/pb_get', {'Referer': 'https://estadisticas.cafci.org.ar/'})
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(raw), read_only=True).worksheets[0]
    pb = {}
    for r in ws.iter_rows(values_only=True):
        if r and isinstance(r[20], (int, float)):
            fecha = None
            if isinstance(r[4], str) and re.match(r'\d\d/\d\d/\d\d', r[4]):
                d, m, y = r[4].split('/'); fecha = f'20{y}-{m}-{d}'
            pb[int(r[20])] = {'fecha': fecha, 'aum': float(r[14] or 0)}
    return cat, pb


def resc_label(n):
    return 'En el día (T+0)' if n == 0 else f'{24 * n} hs'


def dur_label(s):
    return (s or '').replace('Menor o Igual a', 'Hasta').replace('Mayor a 1 y Menor Igual 3 Años', 'Entre 1 y 3 años') \
        .replace('Mayor a 3 Años', 'Más de 3 años').replace('0.5 Año', '6 meses').replace('1 Año', '1 año')


def money(n):
    return f'{n:,.0f}'.replace(',', '.')


def select(cat, pb, ov_usd):
    latest = max((v['fecha'] for v in pb.values() if v['fecha']), default=None)
    fresh_from = (dt.date.fromisoformat(latest) - dt.timedelta(days=10)).isoformat() if latest else '0000'
    out, skipped = [], []
    for f in cat['fondos']:
        if f['estado'] != 1: continue
        g = f['sociedad_gerente']['nombre']
        inst = next((b for b, rx in BANKS if rx.search(g)), None)
        if not inst: continue
        mon = 'USD' if f['moneda']['id'] in (2, 180) else 'ARS' if f['moneda']['id'] == 1 else None
        tipo = f['tipo_renta']['id']
        if not mon or (mon == 'USD' and tipo not in (3, 4)): continue
        aum_total = sum(pb.get(c['id'], {}).get('aum', 0) for c in f['clases'])
        cls_ok = [c for c in f['clases'] if not EXCLUDE_CLASS.search(c['nombre'])
                  and (pb.get(c['id'], {}).get('fecha') or '0') >= fresh_from]
        ov = ov_usd.get(str(f['id'])) if mon == 'USD' else None
        # Solo fondos con Clase A (personas humanas) con inversores y cuotaparte al día
        pick = next((c for c in cls_ok if re.search(r'Clase A$', c['nombre']) and pb[c['id']]['aum'] > 0), None)
        if not pick or aum_total < MIN_AUM[mon]:
            why = 'sin clase A con inversores' if not pick else 'sin actividad'
            skipped.append(f"{f['nombre']} ({INST_LBL[inst]}, {mon}): {why}"); continue
        if ov and ov['clase'] != pick['id']:
            ov = {k: v for k, v in ov.items() if k not in ('cls', 'min', 'note')}
        fam = {4: 'mm', 3: 'rf', 2: 'rv'}.get(tipo, 'otros') if mon == 'ARS' else ('mm' if tipo == 4 else 'rf')
        if mon == 'ARS' and tipo != 4 and DL_RX.search(f['nombre'] + ' ' + (f.get('objetivo') or '')): fam = 'dl'
        tipo_lbl = f['tipo_renta']['nombre']
        sub = (f.get('tipo_renta_mixta') or {}).get('nombre')
        if sub and sub not in ('No Registrado', 'No Aplicable'): tipo_lbl += ' · ' + sub
        mn = pick.get('inversion_minima') or 0
        d = {'id': ('U' if mon == 'USD' else 'P') + str(f['id']), 'cur': mon, 'inst': inst, 'fam': fam,
             'name': f['nombre'], 'cls': pick['nombre'].split(' - ')[-1], 'tipo': tipo_lbl,
             'resc': resc_label(f['dias_liquidacion']), 'online': True, 'note': '',
             'min': 'Sin mínimo' if not mn else ('USD ' if mon == 'USD' else '$ ') + money(mn),
             'dur': dur_label(f['duration']['nombre']), 'cartera': (f.get('objetivo') or '').strip()[:200],
             'aum': round(aum_total), 'f': f['id'], 'c': pick['id'],
             'url': f"https://estadisticas.cafci.org.ar/fondos/{f['id']}?clase={pick['id']}"}
        if ov:
            for k in ('name', 'cls', 'resc', 'online', 'note', 'min', 'dur', 'cartera'):
                if ov.get(k) not in (None, ''): d[k] = ov[k]
        out.append(d)
    return out, skipped


def risk(vals):
    pk, mdd = -1, 0.0
    for _, v in vals:
        pk = max(pk, v); mdd = min(mdd, v / pk - 1)
    rs = [math.log(vals[i][1] / vals[i - 1][1]) for i in range(1, len(vals)) if vals[i - 1][1] > 0 and vals[i][1] > 0]
    vol = None
    if len(rs) > 2:
        m = sum(rs) / len(rs)
        vol = round(math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1)) * math.sqrt(52) * 100, 2)
    return round(mdd * 100, 2) + 0.0, vol


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'data', 'data.json')
    ov_usd = json.load(open(os.path.join(HERE, 'overrides_usd.json')))
    cat, pb = load_sources()
    funds, skipped = select(cat, pb, ov_usd)
    failed = {}
    ser = {}
    for i in range(0, len(funds), 4):
        chunk = funds[i:i + 4]
        try:
            ser.update(cafci_src.series([(x['f'], x['c'], x['cls'].split(' (')[0]) for x in chunk]))
        except Exception as e:
            for x in chunk: failed.setdefault(x['id'], f'serie: {type(e).__name__}')
        time.sleep(0.4)
    final = []
    for x in funds:
        try:
            fi = cafci_src.ficha(x['f'], x['c'])
        except Exception as e:
            failed[x['id']] = f'ficha: {type(e).__name__}: {e}'; continue
        x.update({'asOf': fi['asOf'], 'r1m': fi['r1m'], 'r3m': fi['r3m'], 'r12m': fi['r12m']})
        s = ser.get((x['f'], x['c']))
        x['mdd'] = x['vol'] = None; x['h'] = []
        if s and len(s) > 3:
            vals = [(d, 1 + p / 100) for d, p in s]
            # saltar el tramo plano previo al lanzamiento
            k0 = 0
            while k0 + 1 < len(vals) and vals[k0 + 1][1] == vals[k0][1]: k0 += 1
            vals = vals[k0:]
            x['mdd'], x['vol'] = risk(vals)
            x['riskAsOf'] = vals[-1][0]
            x['h'] = [[d[2:].replace('-', ''), round((v / vals[0][1] - 1) * 100, 3)] for d, v in vals]
        x['sharpe'] = round(x['r12m'] / x['vol'], 2) if x.get('r12m') is not None and x.get('vol') else None
        final.append(x)
        time.sleep(0.25)
    now = dt.datetime.now(TZ).isoformat(timespec='seconds')
    data = {'generatedAt': now, 'source': 'CAFCI', 'funds': final, 'skipped': sorted(skipped), 'failed': failed}
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    json.dump(data, open(dst, 'w'), ensure_ascii=False, separators=(',', ':'))
    print(f"{len(final)} fondos ({sum(1 for f in final if f['cur']=='USD')} USD, {sum(1 for f in final if f['cur']=='ARS')} ARS); "
          f"fallas: {len(failed)}; sin actividad: {len(skipped)}")
    if failed: print(json.dumps(failed, ensure_ascii=False, indent=1))
    if len(final) < 20:
        sys.exit('Muy pocos fondos: no se publica')


if __name__ == '__main__':
    main()
