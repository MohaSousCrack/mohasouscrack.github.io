#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carte precise d'une commune, batiment par batiment : ce que declare l'Arcep,
confronte a ce que repondent les operateurs eux-memes.

  - Bouygues : api.bouyguestelecom.fr, ouverte, sans jeton. Rend par adresse les
    technologies disponibles, la date d'ouverture commerciale, le point de
    mutualisation de rattachement et le code immeuble de l'Arcep.
  - SFR : service ArcGIS public, emprises de couverture avec debit maximum.

L'API de Bouygues n'accepte qu'une petite emprise a la fois : on ne interroge
que les tuiles contenant reellement des batiments, espacees dans le temps.
Compter environ une minute et demie pour une commune moyenne.

    python carte_precise.py 35251
    python carte_precise.py Saint-Aubin-d-Aubigne --sans-sfr

Produit carte-precise-<insee>.html, autonome, a ouvrir dans le navigateur.
"""

import argparse
import collections
import json
import math
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
POINTS = os.path.join(os.path.dirname(HERE), "site-prospection", "points")
BT = "https://api.bouyguestelecom.fr/adresses-couverture-fixe/zones"
SFR_API = "https://www.sfr.fr/api/arcgis-sig/CARTOCOUV_SURFACE/1.0/0/query"

PAS = 0.007          # degres : la plus grande tuile acceptee par Bouygues
PAUSE = 1.5
MAX_TUILES = 400
RAYON = 25           # metres pour apparier un batiment
NOMS = [(1, "Free"), (2, "Orange"), (4, "Bouygues"), (8, "SFR")]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(m):
    print(m, flush=True)


def sans_accent(s):
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def trouver_commune(cle):
    p = os.path.join(POINTS, cle + ".json")
    if os.path.exists(p):
        return cle, json.load(open(p, encoding="utf-8"))
    cible = sans_accent(cle)
    for nom in sorted(os.listdir(POINTS)):
        if not nom.endswith(".json") or nom == "index.json":
            continue
        d = json.load(open(os.path.join(POINTS, nom), encoding="utf-8"))
        if sans_accent(d.get("nom", "")) == cible:
            return d["insee"], d
    return None, None


def metres(lat1, lon1, lat2, lon2):
    r = math.radians
    h = (math.sin(r(lat2 - lat1) / 2) ** 2 +
         math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def http(url, entetes):
    req = urllib.request.Request(url, headers=entetes)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def bouygues(tuiles):
    """Adresses Bouygues du secteur, indexees par code immeuble Arcep."""
    entetes = {"Accept": "application/json",
               "Origin": "https://www.bouyguestelecom.fr",
               "Referer": "https://www.bouyguestelecom.fr/",
               "x-source": "portailvente_weto", "x-version": "1",
               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/151.0.0.0 Safari/537.36"}
    trouve, erreurs = {}, 0

    def une(t):
        lat, lon = t
        q = urllib.parse.urlencode({
            "coordonneeX": lon, "coordonneeY": lat,
            "coordonneeXmin": lon - PAS / 2, "coordonneeXmax": lon + PAS / 2,
            "coordonneeYmin": lat - PAS / 2, "coordonneeYmax": lat + PAS / 2,
            "vueDetaillee": "true"})
        for essai in range(2):
            try:
                return http(BT + "?" + q, entetes)
            except Exception:
                time.sleep(1 + essai)
        return None

    # Quelques tuiles de front plutot qu'une file d'attente : on divise le temps
    # par quatre sans matraquer le service.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        reponses = list(pool.map(une, tuiles))

    for i, rep in enumerate(reponses, 1):
        if rep is None:
            erreurs += 1
            continue
        for a in rep if isinstance(rep, list) else []:
            for b in a.get("batiments") or []:
                c = b.get("coordonneesXY") or {}
                if c.get("coordonneeY") is None:
                    continue
                trouve[b.get("codeImmeuble") or f"{c['coordonneeY']},{c['coordonneeX']}"] = {
                    "lat": c["coordonneeY"], "lon": c["coordonneeX"],
                    "adresse": a.get("adresseLineaire", ""),
                    "technos": b.get("technosDisponibles") or [],
                    "ouverture": (b.get("dateOuvertureCommerciale") or "")[:10],
                    "pm": b.get("referencePM"), "etat": b.get("etatImmeuble"),
                    "logements": b.get("nombreLogements"),
                    "sature": b.get("batimentSature"),
                    "oi": ((b.get("operateurImmeuble") or {}).get("libelle") or ""),
                }
    log(f"     {len(tuiles)} tuiles, {len(trouve)} bâtiments")
    if erreurs:
        log(f"     ({erreurs} tuiles en échec)")
    return trouve


def sfr(lats, lons):
    env = {"xmin": min(lons) - .01, "ymin": min(lats) - .01,
           "xmax": max(lons) + .01, "ymax": max(lats) + .01,
           "spatialReference": {"wkid": 4326}}
    q = urllib.parse.urlencode({
        "f": "json", "geometry": json.dumps(env),
        "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "max_downstream",
        "where": "1=1", "returnGeometry": "true"})
    d = http(SFR_API + "?" + q, {
        "Accept": "application/json",
        "Referer": "https://www.sfr.fr/carte-couverture-reseau-sfr-fibre-optique/carteseo.html",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0"})
    zones = []
    for f in d.get("features", []):
        try:
            debit = int(str(f["attributes"].get("max_downstream") or 0))
        except ValueError:
            debit = 0
        for ring in (f.get("geometry") or {}).get("rings", []):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            zones.append((debit, ring, min(xs), min(ys), max(xs), max(ys)))
    return zones


def dans(ring, x, y):
    d = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            d = not d
        j = i
    return d


def debit_sfr(zones, lon, lat):
    best = 0
    for debit, ring, x0, y0, x1, y1 in zones:
        if debit > best and x0 <= lon <= x1 and y0 <= lat <= y1 and dans(ring, lon, lat):
            best = debit
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("commune")
    ap.add_argument("--sans-sfr", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="réinterroger Bouygues au lieu d'utiliser le cache")
    ap.add_argument("--enrichir", action="store_true",
                    help="réinjecter la vérification dans points/<insee>.json, "
                         "que lit Ma tournée")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    insee, d = trouver_commune(args.commune)
    if not d:
        sys.exit(f"{args.commune} : introuvable dans points/")
    bats = d["b"]
    lats = [b[0] for b in bats]
    lons = [b[1] for b in bats]
    nom = d.get("nom") or insee
    log(f"{nom} ({insee}) — {len(bats)} bâtiments de la base Arcep")

    tuiles = sorted({(round((int(b[0] / PAS) + .5) * PAS, 6),
                      round((int(b[1] / PAS) + .5) * PAS, 6)) for b in bats})
    if len(tuiles) > MAX_TUILES:
        sys.exit(f"{len(tuiles)} tuiles nécessaires : trop pour un sondage.")
    # On garde la moisson : regenerer la carte ne doit pas recouter 62 appels.
    cache = os.path.join(HERE, f"_bouygues_{insee}.json")
    if os.path.exists(cache) and not args.refresh:
        bt = json.load(open(cache, encoding="utf-8"))
        log(f"1/3  Bouygues : {len(bt)} bâtiments repris du cache "
            f"(--refresh pour réinterroger)")
    else:
        log(f"1/3  Bouygues : {len(tuiles)} tuiles à interroger "
            f"(~{len(tuiles) * PAUSE / 60:.0f} min)")
        bt = bouygues(tuiles)
        json.dump(bt, open(cache, "w", encoding="utf-8"), ensure_ascii=False)

    zones = []
    if not args.sans_sfr:
        log("2/3  SFR : emprises de couverture")
        try:
            zones = sfr(lats, lons)
            log(f"     {len(zones)} emprises")
        except Exception as e:
            log(f"     échec : {e}")

    log("3/3  Croisement et carte")
    btl = list(bt.values())
    feats, compte = [], collections.Counter()
    for b in bats:
        lat, lon, ops = b[0], b[1], b[5]
        voie = d["voies"][b[2]] if b[2] >= 0 else ""
        adr = ((b[3] + " ") if b[3] else "") + voie
        proche, dmin = None, RAYON
        for x in btl:
            dd = metres(lat, lon, x["lat"], x["lon"])
            if dd < dmin:
                dmin, proche = dd, x
        arcep_b = bool(ops & 4)
        bt_ftth = bool(proche and "FTTH" in proche["technos"])
        if not proche:
            etat = "inconnu"
        elif arcep_b and bt_ftth:
            etat = "accord_oui"
        elif not arcep_b and not bt_ftth:
            etat = "accord_non"
        elif bt_ftth:
            etat = "bouygues_seul"
        else:
            etat = "arcep_seul"
        compte[etat] += 1
        feats.append({
            "lat": lat, "lon": lon, "adr": adr or (proche or {}).get("adresse", ""),
            "arcep": [n for bit, n in NOMS if ops & bit],
            "fibre": bool(ops), "log": b[4],
            "bt": None if not proche else {
                "ftth": bt_ftth, "technos": proche["technos"],
                "ouv": proche["ouverture"], "pm": proche["pm"],
                "sature": proche["sature"], "oi": proche["oi"]},
            "sfr": debit_sfr(zones, lon, lat) if zones else None,
            "etat": etat,
        })

    log(f"     Bouygues — les deux oui {compte['accord_oui']}, "
        f"les deux non {compte['accord_non']}, Bouygues seul {compte['bouygues_seul']}, "
        f"Arcep seul {compte['arcep_seul']}, non apparié {compte['inconnu']}")

    # Reinjection dans le fichier de commune : c'est ce que lit « Ma tournee ».
    # bt[i] vaut 1 si Bouygues annonce la fibre a ce batiment, 0 s'il dit non,
    # -1 s'il ne le connait pas. L'Arcep reste la base, Bouygues la corrige.
    if args.enrichir:
        d["bt"] = [1 if f["etat"] in ("accord_oui", "bouygues_seul") else
                   (-1 if f["etat"] == "inconnu" else 0) for f in feats]
        # sf : meme convention pour SFR, depuis ses emprises de couverture.
        if zones:
            d["sf"] = [1 if (f["sfr"] or 0) >= 1000 else 0 for f in feats]
            d["sfd"] = time.strftime("%Y-%m-%d")
        d["btd"] = time.strftime("%Y-%m-%d")
        chemin = os.path.join(POINTS, f"{insee}.json")
        json.dump(d, open(chemin, "w", encoding="utf-8"),
                  ensure_ascii=False, separators=(",", ":"))
        vrais = sum(1 for x in d["bt"] if x == 1)
        log(f"     {chemin} enrichi : {vrais} bâtiments confirmés chez Bouygues")

    sortie = args.out or os.path.join(HERE, f"carte-precise-{insee}.html")
    with open(os.path.join(HERE, "gabarit_precis.html"), encoding="utf-8") as f:
        tpl = f.read()
    html = (tpl.replace('"__DATA__"', json.dumps(feats, ensure_ascii=False,
                                                 separators=(",", ":")))
               .replace("__NOM__", nom).replace("__INSEE__", insee)
               .replace("__ARCEP__", d.get("arcep", "?")))
    with open(sortie, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"     -> {sortie}")


if __name__ == "__main__":
    main()
