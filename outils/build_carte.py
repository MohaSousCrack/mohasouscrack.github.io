#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Construit une carte HTML autonome de l'eligibilite a la fibre (FTTH) commune
par commune et operateur par operateur, pour tout ce qui est accessible en
transports en commun depuis Rennes.

Sources :
  - Arcep « Ma connexion internet », table d'eligibilite par departement
    https://www.data.gouv.fr/datasets/ma-connexion-internet
  - contours communaux de geo.api.gouv.fr
  - temps de trajet produits par build_temps_trajet.py

Usage :
    python build_temps_trajet.py          # d'abord : temps_rennes.json
    python build_carte.py                 # puis : carte-fibre-rennes.html
    python build_carte.py --minutes 90    # limiter a 1 h 30 de Rennes
    python build_carte.py --refresh       # retelecharger les donnees Arcep
"""

import argparse
import collections
import datetime
import gzip
import json
import math
import os
import re
import struct
import sys
import urllib.request
import zipfile

# --- Parametres ------------------------------------------------------------

RENNES = (48.1113, -1.6800)

# La Bretagne est retenue en entier, desservie ou non par le train et le car :
# beaucoup de communes ne sont accessibles qu'en voiture. Les departements
# voisins ne sont gardes que s'ils sont atteignables en transports.
BRETAGNE = ["35", "22", "29", "56"]
VOISINS = ["44", "53", "50", "49", "72"]
DEPTS = BRETAGNE + VOISINS

# Codes operateur Arcep -> cle interne. Referentiel complet :
# https://data.arcep.fr/fixe/maconnexioninternet/reference/last/operateur/
OPS = [
    ("FREE", "free",     "Free"),
    ("FRTE", "orange",   "Orange"),
    ("BOUY", "bouygues", "Bouygues"),
    ("SFR0", "sfr",      "SFR"),
]
BIT = {code: 1 << i for i, (code, _, _) in enumerate(OPS)}
B_FREE, B_ORAN, B_BOUY, B_SFR = (BIT[c] for c, _, _ in OPS)

ARCEP_BASE = "https://data.arcep.fr/fixe/maconnexioninternet/eligibilite/"
ARCEP_URL = ARCEP_BASE + "{trim}/departement/actuel_{dep}.csv.gz"
GEO_URL = ("https://geo.api.gouv.fr/departements/{dep}/communes"
           "?fields=nom,code,population&format=geojson&geometry=contour")
LEAFLET = {
    "leaflet.js": "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
    "leaflet.css": "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
}

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, flush=True)


def fetch(url, dest, refresh=False):
    if os.path.exists(dest) and not refresh and os.path.getsize(dest) > 0:
        return dest
    log(f"     telechargement {os.path.basename(dest)} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "carte-fibre/1.0"})
    with urllib.request.urlopen(req, timeout=900) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


# Jeu de donnees des deploiements (celui qui alimente cartefibre.arcep.fr). La
# couche ZAPM decrit chaque zone arriere de point de mutualisation, avec la date
# de debut du delai de completude et l'operateur d'infrastructure.
DEPLOIEMENTS = ("https://www.data.gouv.fr/api/1/datasets/"
                "le-marche-du-haut-et-tres-haut-debit-fixe-deploiements/")
REF_OPERATEURS = ("https://data.arcep.fr/fixe/maconnexioninternet/reference/"
                  "last/operateur/operateur.csv")


def dbf(chemin, membre):
    """Lit un .dbf dans un zip et rend la liste des enregistrements."""
    raw = zipfile.ZipFile(chemin).read(membre)
    nrec, hlen, rlen = struct.unpack("<IHH", raw[4:12])
    champs, off = [], 32
    while raw[off] != 0x0D:
        d = raw[off:off + 32]
        champs.append((d[:11].split(b"\0")[0].decode("latin-1"), d[16]))
        off += 32
    for i in range(nrec):
        p = hlen + i * rlen + 1
        rec = {}
        for nom, lg in champs:
            rec[nom] = raw[p:p + lg].decode("latin-1").strip()
            p += lg
        yield rec


def zapm(cache):
    """Par commune : dates de debut du delai de completude et operateur
    d'infrastructure, agreges sur ses zones arriere de PM."""
    req = urllib.request.Request(DEPLOIEMENTS, headers={"User-Agent": "carte-fibre/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        meta = json.load(r)
    res = [x for x in meta.get("resources", []) if "zapm" in (x.get("title") or "").lower()]
    if not res:
        return {}
    res.sort(key=lambda x: x.get("title", ""), reverse=True)
    trim = res[0]["title"].split("-")[0]
    z = fetch(res[0]["url"], os.path.join(cache, f"zapm_{trim}.zip"))
    membre = [n for n in zipfile.ZipFile(z).namelist() if n.lower().endswith(".dbf")][0]

    par_commune = {}
    for r in dbf(z, membre):
        insee = r.get("INSEE_COM")
        if not insee:
            continue
        d = par_commune.setdefault(insee, {"dates": [], "oi": set(), "pm": 0})
        d["pm"] += 1
        if r.get("date_debut"):
            d["dates"].append(r["date_debut"])
        if r.get("CodeOI"):
            d["oi"].add(r["CodeOI"])
    # Le fichier communal du meme jeu de donnees nomme l'operateur
    # d'infrastructure en clair et donne le type de zone (AMII, RIP, ZTD),
    # ce qui explique souvent l'absence d'un operateur.
    # Le referentiel operateurs traduit les codes d'operateur d'infrastructure
    # (THDB -> THD Bretagne, FRTE -> Orange...).
    ref = {}
    try:
        p = fetch(REF_OPERATEURS, os.path.join(cache, "operateur.csv"))
        with open(p, encoding="utf-8", errors="replace") as f:
            f.readline()
            for line in f:
                c = line.rstrip("\n").split(";")
                if len(c) >= 3 and c[0]:
                    ref[c[0]] = c[2]
    except Exception:
        pass
    ZONES = {"zipu": "zone d'initiative publique",
             "zipri": "zone d'initiative privée",
             "ZTD": "zone très dense"}

    noms = {}
    com = [x for x in meta.get("resources", [])
           if (x.get("title") or "").lower().endswith("-commune")]
    if com:
        com.sort(key=lambda x: x.get("title", ""), reverse=True)
        zc = fetch(com[0]["url"], os.path.join(cache, "commune_deploiement.zip"))
        mc = [n for n in zipfile.ZipFile(zc).namelist() if n.lower().endswith(".dbf")][0]
        for r in dbf(zc, mc):
            if r.get("INSEE_COM"):
                noms[r["INSEE_COM"]] = (r.get("zone") or "", r.get("oi") or "")

    out = {}
    for insee, d in par_commune.items():
        dates = sorted(x for x in d["dates"] if len(x) >= 10)
        zone, oi = noms.get(insee, ("", ""))
        codes = [x for x in (oi or "").split("-") if x] or sorted(d["oi"])
        out[insee] = {"pm": d["pm"],
                      "dd1": dates[0] if dates else None,
                      "dd2": dates[-1] if dates else None,
                      "oi": ", ".join(ref.get(c, c) for c in codes[:3]),
                      "zone": ZONES.get(zone, zone)}
    log(f"     {len(out)} communes avec des zones arriere de PM ({trim})")
    return out


def trimestres():
    """Les deux derniers trimestres publies par l'Arcep, du plus ancien au plus
    recent. Comparer les deux permet de reperer les communes fraichement
    fibrees, ou l'absence d'un operateur veut surtout dire « pas encore
    declare »."""
    req = urllib.request.Request(ARCEP_BASE, headers={"User-Agent": "carte-fibre/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        html = r.read().decode("utf-8", "replace")
    qs = sorted(set(re.findall(r'href="(\d{4}_T\d)/index\.html"', html)))
    if len(qs) < 2:
        raise RuntimeError("trimestres Arcep introuvables")
    return qs[-2], qs[-1]


def hav(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# --- Geometries ------------------------------------------------------------

def rdp(points, eps):
    """Simplification Ramer-Douglas-Peucker, iterative (les contours communaux
    comptent parfois plusieurs milliers de points)."""
    n = len(points)
    if n < 3:
        return points
    garder = [False] * n
    garder[0] = garder[n - 1] = True
    pile = [(0, n - 1)]
    while pile:
        i0, i1 = pile.pop()
        x0, y0 = points[i0]
        x1, y1 = points[i1]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy)
        imax, dmax = -1, eps
        for i in range(i0 + 1, i1):
            px, py = points[i]
            if norm == 0:
                d = math.hypot(px - x0, py - y0)
            else:
                d = abs(dy * px - dx * py + x1 * y0 - y1 * x0) / norm
            if d > dmax:
                imax, dmax = i, d
        if imax >= 0:
            garder[imax] = True
            pile.append((i0, imax))
            pile.append((imax, i1))
    return [p for p, k in zip(points, garder) if k]


def simplifier(geom, eps, nd=5):
    def ring(r):
        out = rdp([(p[0], p[1]) for p in r], eps)
        if len(out) < 4:
            out = [(p[0], p[1]) for p in r]
        out = [[round(x, nd), round(y, nd)] for x, y in out]
        if out[0] != out[-1]:
            out.append(out[0])
        return out

    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [ring(r) for r in geom["coordinates"]]}
    if geom["type"] == "MultiPolygon":
        return {"type": "MultiPolygon",
                "coordinates": [[ring(r) for r in poly] for poly in geom["coordinates"]]}
    return geom


def centroide(geom):
    """Centroide (lat, lon) de l'anneau exterieur le plus vaste."""
    if geom["type"] == "Polygon":
        rings = [geom["coordinates"][0]]
    elif geom["type"] == "MultiPolygon":
        rings = [poly[0] for poly in geom["coordinates"]]
    else:
        return None
    best, aire_max = None, -1.0
    for r in rings:
        a = cx = cy = 0.0
        for i in range(len(r) - 1):
            x0, y0 = r[i][0], r[i][1]
            x1, y1 = r[i + 1][0], r[i + 1][1]
            cr = x0 * y1 - x1 * y0
            a += cr
            cx += (x0 + x1) * cr
            cy += (y0 + y1) * cr
        if abs(a) > 1e-12 and abs(a) > aire_max:
            aire_max, best = abs(a), (cy / (3 * a), cx / (3 * a))
    return best


# --- Agregation Arcep ------------------------------------------------------

def agreger(chemin):
    """Agrege un CSV departemental Arcep par code INSEE.

    La table liste une ligne par (adresse, operateur, technologie). On ne garde
    que la fibre de bout en bout (FO) et les quatre FAI nationaux, puis on
    reduit chaque adresse a un masque de bits avant de compter par commune :
    c'est ce qui permet de distinguer « Free passe ici, SFR non » d'un simple
    ecart de volumes.
    """
    masques = {}
    toutes = collections.defaultdict(set)
    with gzip.open(chemin, "rt", encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            c = line.rstrip("\n").split(";")
            if len(c) < 5:
                continue
            addr, op, tech = c[1], c[3], c[4]
            toutes[addr[:5]].add(addr)
            if tech != "FO" or op not in BIT:
                continue
            masques[addr] = masques.get(addr, 0) | BIT[op]

    # Compte des adresses par combinaison exacte d'operateurs : c'est ce qui
    # permet ensuite de repondre a n'importe quel croisement (« SFR absent ET
    # Bouygues absent ») sans revenir aux donnees brutes.
    combi = collections.defaultdict(collections.Counter)

    stats = collections.defaultdict(collections.Counter)
    for addr, m in masques.items():
        s = stats[addr[:5]]
        combi[addr[:5]][m] += 1
        s["ftth"] += 1
        if m & B_FREE: s["free"] += 1
        if m & B_ORAN: s["orange"] += 1
        if m & B_BOUY: s["bouygues"] += 1
        if m & B_SFR:  s["sfr"] += 1
        if m & B_FREE:
            manquants = (not m & B_SFR) + (not m & B_BOUY) + (not m & B_ORAN)
            if not m & B_SFR:  s["fns"] += 1
            if not m & B_BOUY: s["fnb"] += 1
            if not m & B_ORAN: s["fno"] += 1
            if manquants:      s["fav"] += 1
            if manquants == 3: s["fseul"] += 1
        else:
            s["sfree"] += 1

    out = {}
    for insee, s in stats.items():
        out[insee] = {k: int(v) for k, v in s.items()}
        out[insee]["tot"] = len(toutes[insee])
        out[insee]["mk"] = {str(m): int(n) for m, n in combi[insee].items()}
    return out


def compter_ftth(chemin):
    """Nombre d'adresses raccordables FTTH par commune, pour un trimestre."""
    vues = collections.defaultdict(set)
    with gzip.open(chemin, "rt", encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            c = line.rstrip("\n").split(";")
            if len(c) < 5 or c[4] != "FO" or c[3] not in BIT:
                continue
            vues[c[1][:5]].add(c[1])
    return {k: len(v) for k, v in vues.items()}


# --- Programme principal ---------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=150,
                    help="hors Bretagne, temps de trajet max en TER/car depuis "
                         "Rennes, en minutes (defaut 150)")
    ap.add_argument("--cache", default=os.path.join(HERE, "_cache"))
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "carte-fibre-rennes.html"))
    ap.add_argument("--site", action="store_true",
                    help="page destinee au site fusionne : ajoute les liens vers "
                         "index.html et tournee.html")
    ap.add_argument("--eps", type=float, default=0.0006,
                    help="tolerance de simplification des contours, en degres")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)

    log("1/4  Ressources")
    for nom, url in LEAFLET.items():
        fetch(url, os.path.join(args.cache, nom))
    ptemps = os.path.join(HERE, "temps_rennes.json")
    if not os.path.exists(ptemps):
        sys.exit("temps_rennes.json manquant : lancez d'abord build_temps_trajet.py")
    temps = json.load(open(ptemps))
    log(f"     {len(temps)} communes desservies en TER ou en car")

    pvoit = os.path.join(HERE, "temps_voiture.json")
    if not os.path.exists(pvoit):
        sys.exit("temps_voiture.json manquant : lancez d'abord build_temps_voiture.py")
    voiture = json.load(open(pvoit))
    log(f"     {len(voiture)} communes avec un temps de trajet en voiture")

    prec, cur = trimestres()
    log(f"     publications Arcep : {cur} (courante), {prec} (précédente)")
    zones = zapm(args.cache)

    log("2/4  Agregation Arcep")
    stats, avant = {}, {}
    for dep in DEPTS:
        csv = fetch(ARCEP_URL.format(trim=cur, dep=dep),
                    os.path.join(args.cache, f"actuel_{dep}.csv.gz"), args.refresh)
        s = agreger(csv)
        stats.update(s)
        vieux = fetch(ARCEP_URL.format(trim=prec, dep=dep),
                      os.path.join(args.cache, f"{prec}_{dep}.csv.gz"))
        avant.update(compter_ftth(vieux))
        log(f"     dept {dep} : {len(s)} communes")

    log("3/4  Contours et assemblage")
    features = []
    for dep in DEPTS:
        geo = fetch(GEO_URL.format(dep=dep),
                    os.path.join(args.cache, f"communes{dep}.geojson"))
        with open(geo, encoding="utf-8") as f:
            fc = json.load(f)
        for feat in fc["features"]:
            p = feat["properties"]
            code = p["code"]
            tr = temps.get(code)
            vo = voiture.get(code)
            s = stats.get(code)
            if not s or not s.get("ftth"):
                continue
            # Toute la Bretagne ; ailleurs, seulement ce qu'on atteint en TER
            # ou en car dans le temps imparti.
            if dep not in BRETAGNE and (not tr or tr[0] > args.minutes):
                continue
            cen = centroide(feat["geometry"])
            if not cen:
                continue
            props = {"c": code, "n": p["nom"], "dep": dep,
                     "pop": p.get("population") or 0,
                     "t": int(tr[0]) if tr else None,
                     "km": tr[1] if tr else None,
                     "v": int(vo[0]) if vo else None,
                     "vkm": vo[1] if vo else None,
                     "lat": round(cen[0], 5), "lon": round(cen[1], 5)}
            for k in ("ftth", "tot", "free", "orange", "bouygues", "sfr",
                      "fns", "fnb", "fno", "fav", "fseul", "sfree"):
                props[k] = s.get(k, 0)
            props["mk"] = s.get("mk", {})
            # adresses fibrees apparues depuis la publication precedente
            props["neuf"] = max(0, props["ftth"] - avant.get(code, 0))
            # La commune a-t-elle ete verifiee directement chez Bouygues ?
            fp = os.path.join(os.path.dirname(HERE), "site-prospection",
                              "points", code + ".json")
            if os.path.exists(fp):
                try:
                    with open(fp, encoding="utf-8") as f:
                        pj = json.load(f)
                    if pj.get("btd"):
                        props["btd"] = pj["btd"]
                        props["btn"] = sum(1 for x in pj.get("bt", []) if x == 1)
                except Exception:
                    pass
            z = zones.get(code)
            if z:
                props["pm"] = z["pm"]
                props["dd1"] = z["dd1"]
                props["dd2"] = z["dd2"]
                props["oi"] = z["oi"]
                props["zone"] = z["zone"]
            features.append({"type": "Feature",
                             "geometry": simplifier(feat["geometry"], args.eps),
                             "properties": props})

    features.sort(key=lambda f: f["properties"]["n"])
    bzh = sum(1 for f in features if f["properties"]["dep"] in BRETAGNE)
    sans = sum(1 for f in features if f["properties"]["t"] is None)
    log(f"     {len(features)} communes retenues : {bzh} en Bretagne, "
        f"{len(features)-bzh} ailleurs (<= {args.minutes:g} min en TER/car)")
    log(f"     dont {sans} accessibles uniquement en voiture")

    log("4/4  Generation du CSV et du HTML")
    csv_out = os.path.join(HERE, "communes-fibre-rennes.csv")
    cols = ["code_insee", "commune", "dep", "habitants", "voiture_min",
            "route_km", "ter_car_min", "rabattement_km",
            "adr_fibre", "adr_fibre_nouvelles", "adr_total",
            "pct_free", "pct_orange", "pct_bouygues", "pct_sfr",
            "free_sans_sfr", "free_sans_bouygues", "free_sans_orange", "free_seul"]
    with open(csv_out, "w", encoding="utf-8-sig", newline="") as f:
        f.write(";".join(cols) + "\n")
        # Tri par nombre d'adresses concernees, pas par ratio : une commune ou
        # deux adresses seulement sont fibrees afficherait sinon 100 % d'ecart.
        for feat in sorted(features, key=lambda x: (-x["properties"]["fav"],
                                                    -x["properties"]["ftth"])):
            p = feat["properties"]
            pc = lambda k: f'{100 * p[k] / p["ftth"]:.1f}'.replace(".", ",")
            vide = lambda x: "" if x is None else str(x).replace(".", ",")
            f.write(";".join(str(v) for v in [
                p["c"], p["n"], p["dep"], p["pop"],
                vide(p["v"]), vide(p["vkm"]), vide(p["t"]), vide(p["km"]),
                p["ftth"], p["neuf"], p["tot"],
                pc("free"), pc("orange"), pc("bouygues"), pc("sfr"),
                p["fns"], p["fnb"], p["fno"], p["fseul"]]) + "\n")
    log(f"     -> {csv_out}")

    data = json.dumps({"type": "FeatureCollection", "features": features},
                      ensure_ascii=False, separators=(",", ":"))
    lus = {}
    for nom in ("leaflet.js", "leaflet.css"):
        with open(os.path.join(args.cache, nom), encoding="utf-8") as f:
            lus[nom] = f.read()
    with open(os.path.join(HERE, "template.html"), encoding="utf-8") as f:
        tpl = f.read()

    html = (tpl.replace("/*__LEAFLET_CSS__*/", lus["leaflet.css"])
               .replace("/*__LEAFLET_JS__*/", lus["leaflet.js"])
               .replace('"__DATA__"', data)
               .replace('"__SITE__"', "true" if args.site else "false")
               .replace('"__MAJ__"', json.dumps({
                   "arcep": cur.replace("_", " "),
                   "genere": datetime.date.today().isoformat(),
               })))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"     -> {args.out}  ({os.path.getsize(args.out)/1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
