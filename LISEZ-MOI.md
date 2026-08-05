# Site fusionné — MohaBanger2ouf

Les deux outils réunis derrière une page d'accueil unique.

| Fichier | Rôle |
|---|---|
| `index.html` | Page d'accueil : présente les deux outils et explique comment ils se complètent. |
| `tournee.html` | L'app de prospection porte-à-porte (ex-`MohaSousCrack/index.html`). |
| `fibre.html` | La carte d'éligibilité fibre par opérateur. |
| `points/` | 2 619 fichiers, un par commune : toutes les habitations avec leur adresse, leur nombre de logements et les opérateurs qui y vendent la fibre (3,4 M de bâtiments, 128 Mo). |

Rien d'autre n'est nécessaire : trois fichiers, aucune dépendance à installer.
Pour tester en local, double-cliquez `index.html`.

## Les deux sens de circulation

**Carte fibre → tournée.** Ouvrez une commune sur la carte, bouton
« 🏠 Prospecter cette commune ». Le lien est de la forme
`tournee.html#ville=Pacé&lat=48.15&lng=-1.77` : l'app crée la ville au bon
endroit, ou l'active si elle existe déjà, puis nettoie l'URL pour qu'un
rechargement ne la rajoute pas une seconde fois.

**Tournée → carte fibre.** Menu ville → « 🗺️ Fibre à … ». Le lien
`fibre.html#q=Nom` ouvre directement la fiche de la commune.

## Charger une commune depuis l'Arcep

Menu ville → **« 🏠 Charger toutes les maisons (Arcep) »**. L'app lit
`points/<code INSEE>.json` et pose **tous** les bâtiments de la commune.

Deux avantages sur le chargement OpenStreetMap, qui reste disponible en secours :

- **le découpage communal est exact.** La base immeuble de l'Arcep porte le code
  INSEE de chaque bâtiment : plus de points tombés dans la commune d'à côté à
  découper au lasso ;
- **chaque maison arrive documentée** : son adresse, son nombre de logements, et
  les opérateurs qui y commercialisent la fibre.

### Ce que montre la carte

| Aspect du point | Signification |
|---|---|
| Gros, **anneau ambré** | Free vend la fibre ici, au moins un concurrent non |
| Taille normale, anneau blanc | Fibre disponible chez les quatre |
| Petit, gris, plus pâle | Aucune fibre à cette adresse |

L'anneau bascule sur la couleur de l'opérateur du foyer dès que vous le
renseignez : l'information que vous avez récoltée prime sur celle de l'Arcep.
La taille du point, elle, continue de signaler l'occasion.

La bulle d'une maison affiche la ligne exacte, par exemple
*⚡ Fibre : Free, Orange — pas Bouygues, SFR*, et un bandeau **⚡ Free devance**
en haut de l'écran filtre la carte sur ces seules maisons.

Le chargement est **additif et rejouable** : les maisons déjà posées à moins de
25 m d'un bâtiment Arcep sont enrichies plutôt que dupliquées, et relancer le
chargement ne crée aucun doublon. Sur Saint-Aubin-d'Aubigné : 1 815 bâtiments,
dont 1 688 où Free devance et 115 sans fibre.

⚠️ **Cette fonction a besoin que le site soit en ligne.** Ouvert en `file://`,
le navigateur interdit à une page de lire un fichier voisin. L'app le détecte,
le dit, et propose un bouton **Importer** : choisissez
`points/<code INSEE>.json` à la main et le résultat est identique. Le menu
« ⬆ Importer » accepte désormais ce format en plus des sauvegardes et du
GeoJSON.

## Ce que j'ai changé dans l'app de tournée

Vos fichiers d'origine dans `Documents\MohaSousCrack` n'ont pas été touchés :
`tournee.html` est une copie de `index.html` (version du 17/07), modifiée.

1. **Recherche de commune réécrite.** Elle appelait Nominatim, puis, en cas
   d'échec, `api.anthropic.com` — un appel qui ne peut pas aboutir depuis un
   navigateur (pas de clé, pas de CORS). Il échouait silencieusement à chaque
   fois. Remplacé par `geo.api.gouv.fr`, l'annuaire officiel des communes
   françaises : nom exact, centre-bourg, **et recherche par code postal**.
   Nominatim reste en secours. Entrée clavier ↵ ajoutée dans le champ.
2. **Titre cliquable** (« ◀ MohaBanger2ouf ») pour revenir à l'accueil, sans
   ajouter de bouton — le bandeau est déjà serré sur téléphone.
3. **Bloc « Éligibilité fibre »** dans le menu ville.
4. Réception des liens `#ville=…` venant de la carte.

Le reste — stockage local, Overpass et ses quatre serveurs, le Plan B
overpass-turbo, le lasso, le trajet en 2-opt — est inchangé.

## Deux remarques que je n'ai pas tranchées à votre place

- **`user-scalable=no`** dans le `<meta viewport>` bloque le zoom à deux
  doigts sur toute la page. C'est cohérent pour une app carto (la carte a son
  propre zoom) mais ça gêne quiconque a besoin d'agrandir le texte. À vous de
  voir si vous le gardez.
- **Le dossier `MohaSousCrack` contient onze versions intermédiaires**
  (`ma-tournee-v3.html`, `v4-1`, `index1.html.html`…). Rien ne presse, mais
  les archiver dans un sous-dossier éviterait de se tromper de fichier.

## Mettre la carte fibre à jour

La carte est générée depuis `Documents\carte fibre`. Pour régénérer la version
du site, il faut l'option `--site`, qui ajoute les liens vers les deux autres
pages :

```bash
python build_carte.py --cache _cache --site --out "C:/Users/sipho/Documents/site-prospection/fibre.html"
```

Sans `--site`, la carte est construite en version autonome, sans lien — c'est
celle qui reste dans `Documents\carte fibre`.

## Mettre le site en ligne

Tout est en HTML statique : déposez les trois fichiers sur GitHub Pages,
Netlify ou Cloudflare Pages et c'est en ligne. Ça règle au passage le **GPS**,
que Chrome refuse d'activer sur un fichier ouvert en `file://` mais autorise
en `https://`.
