#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Calcule le temps de trajet en voiture depuis Rennes vers chaque commune, via le
service public OSRM (routage sur le reseau OpenStreetMap).

On interroge le service `table`, qui rend en une seule requete les durees d'une
origine vers plusieurs destinations. Les communes sont donc traitees par
paquets, avec une pause entre chaque paquet pour rester correct vis-a-vis d'un
serveur de demonstration gratuit.

Produit temps_voiture.json : { code_insee: [minutes, km] }
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DEPTS = ["35", "22", "29", "56", "44", "53", "50", "49", "72"]
RENNES = (48.1113, -1.6800)          # (lat, lon) — place de la Mairie
SERVEURS = [
    "https://router.project-osrm.org",
    "https://routing.openstreetmap.de/routed-car",
]
PAQUET = 90                           # destinations par requete
PAUSE = 1.0                           # secondes entre deux requetes


def log(m):
    print(m, flush=True)


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


def interroger(coords):
    """coords : [(lat, lon), ...] avec l'origine en premier."""
    pts = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in coords)
    suffixe = f"/table/v1/driving/{pts}?sources=0&annotations=duration,distance"
    derniere = None
    for base in SERVEURS:
        for essai in range(3):
            try:
                req = urllib.request.Request(
                    base + suffixe, headers={"User-Agent": "carte-fibre/1.0"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    d = json.load(r)
                if d.get("code") == "Ok":
                    return d
                derniere = d.get("code")
            except urllib.error.HTTPError as e:
                derniere = f"HTTP {e.code}"
                time.sleep(3 * (essai + 1))       # 429 : on laisse respirer
            except Exception as e:
                derniere = str(e)
                time.sleep(2 * (essai + 1))
    raise RuntimeError(f"routage indisponible ({derniere})")


def main():
    cache = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_cache")
    sortie = os.path.join(HERE, "temps_voiture.json")

    log("1/2  Centroides communaux")
    communes = []
    for dep in DEPTS:
        p = os.path.join(cache, f"communes{dep}.geojson")
        if not os.path.exists(p):
            log(f"     dept {dep} : contours absents, ignore")
            continue
        with open(p, encoding="utf-8") as f:
            fc = json.load(f)
        for feat in fc["features"]:
            c = centroide(feat["geometry"])
            if c:
                communes.append((feat["properties"]["code"], c))
    log(f"     {len(communes)} communes")

    deja = {}
    if os.path.exists(sortie):
        deja = json.load(open(sortie))
        communes = [c for c in communes if c[0] not in deja]
        log(f"     {len(deja)} deja calculees, {len(communes)} restantes")

    log(f"2/2  Routage OSRM, {(len(communes) + PAQUET - 1) // PAQUET} requetes")
    res = dict(deja)
    for i in range(0, len(communes), PAQUET):
        lot = communes[i:i + PAQUET]
        d = interroger([RENNES] + [c for _, c in lot])
        durees = d["durations"][0][1:]
        dists = d.get("distances", [[None] * (len(lot) + 1)])[0][1:]
        for (code, _), sec, m in zip(lot, durees, dists):
            if sec is None:
                continue
            res[code] = [round(sec / 60), round(m / 1000, 1) if m else None]
        log(f"     {min(i + PAQUET, len(communes))}/{len(communes)}")
        json.dump(res, open(sortie, "w"), separators=(",", ":"))
        time.sleep(PAUSE)

    vals = sorted(v[0] for v in res.values())
    log(f"     {len(res)} communes routees")
    if vals:
        log(f"     mediane {vals[len(vals)//2]} min, maximum {vals[-1]} min")
    log(f"     -> {sortie}")


if __name__ == "__main__":
    main()
