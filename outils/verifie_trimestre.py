#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Compare le dernier trimestre publie par l'Arcep avec celui deja embarque dans
fibre.html. Sert de garde au robot de mise a jour : sans nouvelle publication,
inutile de telecharger 800 Mo pour rien.

Ecrit `nouveau=true|false`, `publie=...` et `en_ligne=...` dans le fichier
designe par GITHUB_OUTPUT, et affiche le verdict.
"""

import os
import re
import sys
import urllib.request

INDEX = "https://data.arcep.fr/fixe/maconnexioninternet/eligibilite/"
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "fibre.html")


def dernier_trimestre():
    req = urllib.request.Request(INDEX, headers={"User-Agent": "carte-fibre/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        html = r.read().decode("utf-8", "replace")
    qs = sorted(set(re.findall(r'href="(\d{4}_T\d)/index\.html"', html)))
    if not qs:
        raise SystemExit("index Arcep illisible")
    return qs[-1]


def trimestre_en_ligne():
    if not os.path.exists(PAGE):
        return ""
    # La declaration se trouve apres les donnees embarquees, donc largement
    # au-dela du debut du fichier : on lit tout, quitte a charger trois Mo.
    with open(PAGE, encoding="utf-8", errors="replace") as f:
        page = f.read()
    m = re.search(r'const MAJ = \{"arcep":\s*"([^"]+)"', page)
    return m.group(1).replace(" ", "_") if m else ""


def main():
    publie = dernier_trimestre()
    actuel = trimestre_en_ligne()
    force = os.environ.get("FORCER", "").lower() == "true"
    nouveau = force or publie != actuel

    print(f"Arcep publie : {publie}")
    print(f"site         : {actuel or '(inconnu)'}")
    print("verdict      : " + ("reconstruction" + (" forcee" if force else "")
                               if nouveau else "rien de neuf, on s'arrete la"))

    sortie = os.environ.get("GITHUB_OUTPUT")
    if sortie:
        with open(sortie, "a", encoding="utf-8") as f:
            f.write(f"nouveau={'true' if nouveau else 'false'}\n")
            f.write(f"publie={publie}\n")
            f.write(f"en_ligne={actuel}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
