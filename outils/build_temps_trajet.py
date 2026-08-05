#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Calcule, pour chaque commune de l'Ouest, le meilleur temps de trajet en
transports en commun depuis la gare de Rennes, a partir des GTFS ouverts :

  - KORRIGO : agregat des reseaux urbains et interurbains de Bretagne
              (TER BreizhGo, cars BreizhGo, STAR, Bibus, CTRL...)
  - SNCF    : TGV, Intercites et TER au niveau national

Methode : RAPTOR (Round-bAsed Public Transit Optimized Router) lance depuis
la gare de Rennes pour une serie d'heures de depart d'un jeudi type, avec au
plus MAX_ROUNDS-1 correspondances. On retient pour chaque arret le meilleur
temps de porte a porte, puis pour chaque commune le meilleur de ses arrets.

Produit temps_rennes.json : { code_insee: minutes }
"""

import bisect
import collections
import csv
import io
import json
import math
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Jeudi ou les deux GTFS sont simultanement les mieux servis. Les reseaux
# urbains de KORRIGO ne sont detailles que jusqu'a fin aout dans cette version
# du flux : au-dela, les temps de trajet seraient sous-estimes faute de lignes.
JOUR = "20260827"
DEPARTS = [h * 60 + m for h in range(5, 21) for m in (0, 30)]
MAX_ROUNDS = 4               # 3 correspondances maximum
MAX_MINUTES = 240            # on ne garde rien au-dela de 4 h
GARE_RENNES = (48.10345, -1.67235)
RAYON_ORIGINE = 600          # metres autour de la gare = points de depart
RAYON_CORRESP = 300          # metres pour une correspondance a pied
VITESSE_MARCHE = 5000 / 60   # metres par minute (5 km/h)
PENALITE_CORRESP = 3         # minutes forfaitaires par correspondance


def log(msg):
    print(msg, flush=True)


def hav(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def hhmmss(t):
    """'25:04:00' -> minutes depuis minuit (peut depasser 1440)."""
    try:
        h, m, s = t.split(":")
        return int(h) * 60 + int(m) + (1 if int(s) >= 30 else 0)
    except Exception:
        return None


def reader(zf, name):
    with zf.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig", "replace"))


# --- Chargement des GTFS ---------------------------------------------------

def charger(chemin, prefixe):
    """Retourne (stops, trips_actifs, sequences) pour le jour de reference."""
    zf = zipfile.ZipFile(chemin)
    noms = set(zf.namelist())

    stops = {}
    for r in reader(zf, "stops.txt"):
        if r.get("location_type") not in (None, "", "0"):
            continue
        try:
            lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
        except (ValueError, KeyError, TypeError):
            continue
        stops[prefixe + r["stop_id"]] = (r.get("stop_name", ""), lat, lon)

    # services actifs le jour de reference
    actifs = set()
    if "calendar.txt" in noms:
        jours = ["monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday"]
        import datetime
        d = datetime.date(int(JOUR[:4]), int(JOUR[4:6]), int(JOUR[6:]))
        col = jours[d.weekday()]
        for r in reader(zf, "calendar.txt"):
            if r.get(col) == "1" and r["start_date"] <= JOUR <= r["end_date"]:
                actifs.add(r["service_id"])
    if "calendar_dates.txt" in noms:
        for r in reader(zf, "calendar_dates.txt"):
            if r["date"] != JOUR:
                continue
            if r["exception_type"] == "1":
                actifs.add(r["service_id"])
            else:
                actifs.discard(r["service_id"])

    trips = set()
    for r in reader(zf, "trips.txt"):
        if r["service_id"] in actifs:
            trips.add(r["trip_id"])

    # horaires des courses actives
    seqs = collections.defaultdict(list)
    for r in reader(zf, "stop_times.txt"):
        tid = r["trip_id"]
        if tid not in trips:
            continue
        arr = hhmmss(r.get("arrival_time") or r.get("departure_time") or "")
        dep = hhmmss(r.get("departure_time") or r.get("arrival_time") or "")
        if arr is None or dep is None:
            continue
        sid = prefixe + r["stop_id"]
        if sid not in stops:
            continue
        seqs[prefixe + tid].append((int(r["stop_sequence"]), sid, arr, dep))

    transfers = []
    if "transfers.txt" in noms:
        for r in reader(zf, "transfers.txt"):
            a = prefixe + (r.get("from_stop_id") or "")
            b = prefixe + (r.get("to_stop_id") or "")
            if a in stops and b in stops and a != b:
                t = r.get("min_transfer_time") or ""
                transfers.append((a, b, max(2, int(int(t) / 60)) if t.isdigit() else 3))

    for k in seqs:
        seqs[k].sort()
    log(f"     {os.path.basename(chemin)} : {len(stops)} arrets, "
        f"{len(seqs)} courses le {JOUR}")
    return stops, seqs, transfers


# --- Construction du reseau RAPTOR ----------------------------------------

def construire(feeds):
    stops, seqs, transfers = {}, {}, []
    for chemin, prefixe in feeds:
        s, q, t = charger(chemin, prefixe)
        stops.update(s)
        seqs.update(q)
        transfers += t

    # regroupement des courses en « routes » (memes sequences d'arrets)
    groupes = collections.defaultdict(list)
    for tid, seq in seqs.items():
        pattern = tuple(s for _, s, _, _ in seq)
        if len(pattern) < 2:
            continue
        groupes[pattern].append(([a for _, _, a, _ in seq],
                                 [d for _, _, _, d in seq]))

    # Chaque route porte : le motif d'arrets, les horaires de ses courses
    # triees par heure de depart, et — pour la recherche binaire du RAPTOR —
    # la colonne des heures de depart a chaque position.
    routes = []
    for pattern, courses in groupes.items():
        courses.sort(key=lambda ad: ad[1][0])
        arrs = [a for a, _ in courses]
        deps = [d for _, d in courses]
        depcol = [[d[pos] for d in deps] for pos in range(len(pattern))]
        routes.append((pattern, arrs, deps, depcol))

    # index arret -> (index de route, position dans la route)
    par_arret = collections.defaultdict(list)
    for ri, (pattern, _, _, _) in enumerate(routes):
        for pos, sid in enumerate(pattern):
            par_arret[sid].append((ri, pos))

    # correspondances a pied : grille spatiale ~300 m
    cell = 0.004
    grille = collections.defaultdict(list)
    for sid, (_, lat, lon) in stops.items():
        grille[(int(lat / cell), int(lon / cell))].append(sid)
    pieds = collections.defaultdict(dict)
    for a, b, m in transfers:
        pieds[a][b] = min(pieds[a].get(b, 999), m)
    for sid, (_, lat, lon) in stops.items():
        ci, cj = int(lat / cell), int(lon / cell)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for autre in grille.get((ci + di, cj + dj), ()):
                    if autre == sid:
                        continue
                    d = hav((lat, lon), stops[autre][1:])
                    if d <= RAYON_CORRESP:
                        m = max(2, int(d / VITESSE_MARCHE) + 1)
                        if m < pieds[sid].get(autre, 999):
                            pieds[sid][autre] = m
    pieds = {k: sorted(v.items()) for k, v in pieds.items()}

    log(f"     reseau : {len(stops)} arrets, {len(routes)} routes, "
        f"{sum(len(v) for v in pieds.values())} correspondances pietonnes")
    return stops, routes, par_arret, pieds


def raptor(depart, origines, routes, par_arret, pieds):
    """Arrivee au plus tot depuis les arrets d'origine, en MAX_ROUNDS rondes."""
    INF = float("inf")
    best = collections.defaultdict(lambda: INF)
    marques = set()
    for sid in origines:
        best[sid] = depart
        marques.add(sid)

    for _ in range(MAX_ROUNDS):
        # routes a explorer
        a_faire = {}
        for sid in marques:
            for ri, pos in par_arret.get(sid, ()):
                if pos < a_faire.get(ri, 10 ** 9):
                    a_faire[ri] = pos
        marques = set()

        for ri, pos0 in a_faire.items():
            pattern, arrs, deps, depcol = routes[ri]
            ct = None                          # index de la course prise
            for pos in range(pos0, len(pattern)):
                sid = pattern[pos]
                if ct is not None and arrs[ct][pos] < best[sid]:
                    best[sid] = arrs[ct][pos]
                    marques.add(sid)
                # peut-on monter dans une course plus tot a cet arret ?
                pret = best[sid]
                if pret < INF and (ct is None or pret <= deps[ct][pos]):
                    i = bisect.bisect_left(depcol[pos], pret)
                    if i < len(deps) and (ct is None or i < ct):
                        ct = i

        # correspondances a pied
        for sid in list(marques):
            t = best[sid]
            for autre, m in pieds.get(sid, ()):
                if t + m + PENALITE_CORRESP < best[autre]:
                    best[autre] = t + m + PENALITE_CORRESP
                    marques.add(autre)
        if not marques:
            break
    return best


# --- Rattachement des arrets aux communes ---------------------------------

def anneaux(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def centroide(rings):
    """Centroide (lat, lon) de l'anneau le plus vaste — l'API geo ne renvoie
    pas le champ « centre » quand on lui demande la geometrie du contour."""
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
        if abs(a) < 1e-12:
            continue
        if abs(a) > aire_max:
            aire_max = abs(a)
            best = (cy / (3 * a), cx / (3 * a))          # (lat, lon)
    if best is None:
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        best = ((min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2)
    return best


def dans(ring, lon, lat):
    dedans = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                dedans = not dedans
        j = i
    return dedans


RAYON_RABATTEMENT = 6000     # metres : commune sans arret, mais desservie a cote


def rattacher(stops, temps, communes, centres):
    """Temps de trajet par commune.

    communes : liste de (insee, anneaux, bbox) ; centres : {insee: (lat, lon)}.
    Retourne {insee: [minutes, km jusqu'a l'arret]}, la distance valant 0 quand
    l'arret se trouve dans la commune elle-meme.
    """
    cell = 0.05
    grille = collections.defaultdict(list)
    for idx, (insee, rings, bbox) in enumerate(communes):
        x0, y0, x1, y1 = bbox
        for i in range(int(y0 / cell), int(y1 / cell) + 1):
            for j in range(int(x0 / cell), int(x1 / cell) + 1):
                grille[(i, j)].append(idx)

    res = {}
    atteints = []
    for sid, t in temps.items():
        if sid not in stops:
            continue
        _, lat, lon = stops[sid]
        atteints.append((lat, lon, t))
        for idx in grille.get((int(lat / cell), int(lon / cell)), ()):
            insee, rings, (x0, y0, x1, y1) = communes[idx]
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if any(dans(r, lon, lat) for r in rings):
                if t < res.get(insee, (10 ** 9,))[0]:
                    res[insee] = (t, 0.0)
                break

    # Communes sans arret : on retient le meilleur arret a moins de 6 km, ce qui
    # correspond a un rabattement realiste a velo ou en depose-minute.
    gstops = collections.defaultdict(list)
    for lat, lon, t in atteints:
        gstops[(int(lat / cell), int(lon / cell))].append((lat, lon, t))
    for insee, rings, bbox in communes:
        if insee in res or insee not in centres:
            continue
        lat, lon = centres[insee]
        ci, cj = int(lat / cell), int(lon / cell)
        best = None
        for di in (-2, -1, 0, 1, 2):
            for dj in (-2, -1, 0, 1, 2):
                for slat, slon, t in gstops.get((ci + di, cj + dj), ()):
                    d = hav((lat, lon), (slat, slon))
                    if d <= RAYON_RABATTEMENT and (best is None or t < best[0]):
                        best = (t, d)
        if best:
            res[insee] = (best[0], round(best[1] / 1000, 1))
    return {k: [v[0], v[1]] for k, v in res.items()}


def main():
    cache = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_cache")
    sortie = os.path.join(HERE, "temps_rennes.json")

    log("1/3  Chargement des GTFS")
    feeds = [(os.path.join(cache, "gtfs", "korrigo.zip"), "K:"),
             (os.path.join(cache, "gtfs", "sncf.zip"), "S:")]
    feeds = [f for f in feeds if os.path.exists(f[0])]
    stops, routes, par_arret, pieds = construire(feeds)

    origines = [s for s, (_, la, lo) in stops.items()
                if hav(GARE_RENNES, (la, lo)) <= RAYON_ORIGINE]
    log(f"     {len(origines)} arrets d'origine autour de la gare de Rennes")

    log("2/3  RAPTOR sur %d heures de depart" % len(DEPARTS))
    meilleur = {}
    for k, dep in enumerate(DEPARTS):
        best = raptor(dep, origines, routes, par_arret, pieds)
        for sid, arr in best.items():
            d = arr - dep
            if 0 <= d <= MAX_MINUTES and d < meilleur.get(sid, 10 ** 9):
                meilleur[sid] = d
        if (k + 1) % 8 == 0:
            log(f"     {k+1}/{len(DEPARTS)} departs traites, "
                f"{len(meilleur)} arrets atteints")
    log(f"     {len(meilleur)} arrets atteints en moins de {MAX_MINUTES} min")

    log("3/3  Rattachement aux communes")
    communes, centres = [], {}
    for dep in ["35", "22", "29", "56", "44", "53", "50", "49", "72"]:
        p = os.path.join(cache, f"communes{dep}.geojson")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            fc = json.load(f)
        for feat in fc["features"]:
            rings = anneaux(feat["geometry"])
            if not rings:
                continue
            code = feat["properties"]["code"]
            xs = [p[0] for r in rings for p in r]
            ys = [p[1] for r in rings for p in r]
            communes.append((code, rings, (min(xs), min(ys), max(xs), max(ys))))
            centres[code] = centroide(rings)
    res = rattacher(stops, meilleur, communes, centres)
    json.dump(res, open(sortie, "w"), separators=(",", ":"))

    tranches = collections.Counter()
    directes = sum(1 for v in res.values() if v[1] == 0)
    for t, _ in res.values():
        tranches[min(int(t // 30) * 30, 240)] += 1
    log(f"     {len(res)} communes atteintes / {len(communes)} analysees "
        f"({directes} avec un arret sur la commune, "
        f"{len(res)-directes} par rabattement < 6 km)")
    for k in sorted(tranches):
        log(f"       {k:>3}-{k+30:<3} min : {tranches[k]:>4} communes")
    log(f"     -> {sortie}")


if __name__ == "__main__":
    main()
