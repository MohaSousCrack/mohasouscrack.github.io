#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Produit, commune par commune, la liste geolocalisee de **toutes** les
habitations, avec pour chacune son adresse, son nombre de logements et les
operateurs qui y commercialisent la fibre.

Deux fichiers Arcep sont croises :
  - la base immeuble       : coordonnees, adresse, nombre de logements, commune ;
  - la table d'eligibilite : quel operateur commercialise la fibre, par immeuble.

C'est la base immeuble qui porte le code INSEE de chaque batiment : le decoupage
communal est donc exact, sans requete Overpass ni rattrapage au lasso.

Sortie : un fichier <site>/points/<insee>.json par commune, dans un format
compact — le GeoJSON pese cinq fois plus pour la meme information.

    {"v":1,"arcep":"2026_T1","insee":"35251","nom":"...",
     "voies":["Rue de Chasné", ...],
     "b":[[lat, lon, index_voie, "numero", logements, operateurs], ...]}

`operateurs` est un masque de bits : 1 Free, 2 Orange, 4 Bouygues, 8 SFR.
Zero signifie qu'aucun des quatre ne commercialise la fibre a cette adresse.

Usage :
    python build_points.py --cache _cache --out ../site-prospection/points
"""

import argparse
import collections
import gzip
import json
import math
import os
import re
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

DEPTS = ["35", "22", "29", "56", "44", "53", "50", "49", "72"]
OPS = {"FREE": 1, "FRTE": 2, "BOUY": 4, "SFR0": 8}
MINI = 20                      # communes en dessous : sans interet pour une tournee

BASE = "https://data.arcep.fr/fixe/maconnexioninternet/"
ELIG = BASE + "eligibilite/{trim}/departement/actuel_{dep}.csv.gz"
IMB = BASE + "base_imb/last/departement/base_imb_{dep}.csv.gz"


def log(m):
    print(m, flush=True)


def fetch(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
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


def trimestre_courant():
    req = urllib.request.Request(BASE + "eligibilite/",
                                 headers={"User-Agent": "carte-fibre/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        html = r.read().decode("utf-8", "replace")
    return sorted(set(re.findall(r'href="(\d{4}_T\d)/index\.html"', html)))[-1]


def wgs84(x, y):
    """Web Mercator (EPSG:3857), l'unite de la base immeuble, vers lat/lon."""
    lon = x / 20037508.34 * 180.0
    lat = math.degrees(2 * math.atan(math.exp(y / 20037508.34 * math.pi)) - math.pi / 2)
    return lat, lon


def masques(chemin):
    """imb_id -> masque des operateurs commercialisant la fibre a cet immeuble."""
    m = collections.defaultdict(int)
    with gzip.open(chemin, "rt", encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            c = line.rstrip("\n").split(";")
            if len(c) < 5 or c[4] != "FO":
                continue
            bit = OPS.get(c[3])
            if bit:
                m[c[0]] |= bit
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.join(HERE, "_cache"))
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(HERE), "site-prospection", "points"))
    args = ap.parse_args()
    os.makedirs(args.cache, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)

    for vieux in os.listdir(args.out):          # l'ancien format n'a plus cours
        if vieux.endswith(".geojson"):
            os.remove(os.path.join(args.out, vieux))

    trim = trimestre_courant()
    log(f"1/3  Publication Arcep : {trim}")

    index = {}
    total_b = total_fibre = total_avantage = 0
    for dep in DEPTS:
        log(f"2/3  Departement {dep}")
        elig = fetch(ELIG.format(trim=trim, dep=dep),
                     os.path.join(args.cache, f"actuel_{dep}.csv.gz"))
        imb = fetch(IMB.format(dep=dep),
                    os.path.join(args.cache, f"base_imb_{dep}.csv.gz"))
        fibre = masques(elig)

        # par commune : dictionnaire des voies + liste des batiments
        voies = collections.defaultdict(dict)
        parc = collections.defaultdict(list)
        noms = {}
        with gzip.open(imb, "rt", encoding="utf-8", errors="replace") as f:
            f.readline()
            for line in f:
                c = line.rstrip("\n").split(";")
                if len(c) < 16:
                    continue
                insee = c[4]
                try:
                    lat, lon = wgs84(float(c[1]), float(c[2]))
                except ValueError:
                    continue
                voie = (c[12] or c[13] or "").strip()
                iv = -1
                if voie:
                    d = voies[insee]
                    if voie not in d:
                        d[voie] = len(d)
                    iv = d[voie]
                num = (c[10] or "").strip()
                if num == "0":
                    num = ""
                num += (c[11] or "").strip()
                try:
                    nlog = int(c[5] or 1)
                except ValueError:
                    nlog = 1
                ops = fibre.get(c[0], 0)
                parc[insee].append([round(lat, 6), round(lon, 6), iv, num,
                                    nlog if nlog > 1 else 1, ops])
                if c[15]:
                    noms[insee] = c[15]

        gardees = 0
        for insee, b in parc.items():
            if len(b) < MINI:
                continue
            liste = [""] * len(voies[insee])
            for v, i in voies[insee].items():
                liste[i] = v
            fic = {"v": 1, "arcep": trim, "insee": insee,
                   "nom": noms.get(insee, ""), "voies": liste, "b": b}
            with open(os.path.join(args.out, f"{insee}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(fic, f, ensure_ascii=False, separators=(",", ":"))
            nf = sum(1 for x in b if x[5])
            na = sum(1 for x in b if (x[5] & 1) and (x[5] & 14) != 14)
            index[insee] = [len(b), nf, na]
            total_b += len(b); total_fibre += nf; total_avantage += na
            gardees += 1
        log(f"     {gardees} communes ecrites")

    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"arcep": trim, "communes": index}, f, separators=(",", ":"))

    poids = sum(os.path.getsize(os.path.join(args.out, n))
                for n in os.listdir(args.out))
    log(f"3/3  {len(index)} communes, {total_b} batiments")
    log(f"     dont {total_fibre} fibres, {total_avantage} ou Free passe "
        f"et pas un concurrent")
    log(f"     {poids/1e6:.0f} Mo  ->  {args.out}")


if __name__ == "__main__":
    main()
