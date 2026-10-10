# JET Gold Intelligence (gratuit, tourne dans le cloud, PC éteint)

Il surveille l'or en permanence et t'envoie une notification sur Android uniquement pour les **trades SCALP et SWING notés 4 ou 5 étoiles**. Il n'ouvre aucun ordre.

## Ce qu'il analyse
1. **Technique SMC sur l'or** : biais des unités de temps supérieures, sweep de liquidité, MSS, OB/FVG, OTE. Scalp = M5 (biais M15/H1), swing = H1 (biais H4/D1).
2. **Actifs liés à l'or** : dollar (DXY), rendement US 10 ans, EURUSD, USDJPY, AUDUSD, argent, pétrole, S&P 500, VIX. Chacun est pondéré selon son effet sur l'or, court terme pour le scalp, 5-20 jours pour le swing.
3. **Calendrier économique** : toutes les annonces USD (CPI, NFP, FOMC, PCE...) et les discours des membres de la Fed. Avant une annonce forte, le scalp est bloqué (30 min avant / 30 min après), le swing aussi (2 h avant).
4. **Actualités** : titres de FXStreet, de la Fed et de Google News sur l'or, le dollar, l'inflation, les banques centrales, la géopolitique. Il lit aussi le texte des discours de la Fed (ton colombe ou faucon).

## Comment la note est calculée
Note finale = note technique (0 à 5) puis ajustement macro :
- macro dans le même sens que le trade : **+1**
- macro neutre : 0
- macro légèrement contre : **-1** ; franchement contre : **-2**
- annonce USD forte proche : **plafonné à 3** (donc pas d'alerte)

Alerte seulement si la note finale est **4 ou 5**. Un 5/5 demande donc une technique à 4 critères avec la macro qui confirme.

## Ce que tu reçois
```
JET GOLD BUY 5* SCALP
Tech 4/5 [BSMP.] FVG  |  Macro +60 (DXY↓ US10Y↓ ARGENT↑)
Entrée : au prix de TON Gold chez le broker, puis applique ces distances :
SL -6.8$   TP1 +10.2$   TP2 +20.4$
Prochaine annonce USD : CPI dans 3h10
```
Tu ouvres MT5, tu regardes le prix réel de ton Gold chez Raise FX et tu appliques les distances. Les prix du bot viennent de Yahoo (future GC=F), qui a quelques dollars d'écart avec ton Gold : c'est pour ça que les niveaux sont donnés en distance et pas en prix.

Tu reçois aussi une alerte « annonce USD dans X min » 45 minutes avant chaque annonce forte.

Le fichier `state/rapport.md` dans ton dépôt GitHub se met à jour à chaque passage : biais macro, actifs liés, annonces des 48 h (avec l'heure de Tahiti), titres qui pèsent sur l'or. Ouvre-le sur ton téléphone quand tu veux le contexte.

## Installation (environ 10 minutes)
1. Appli **ntfy** (Play Store) : « + », saisis un nom de sujet secret et long (par exemple `jet-gold-tapu-7q2m9x`), puis S'abonner.
2. Sur **github.com** : crée un dépôt **public** (ex. `jet-gold`) et dépose tout le contenu de ce dossier, **y compris le dossier `.github`** (sur Mac, Cmd+Maj+. affiche les fichiers cachés). Public = minutes d'exécution gratuites et illimitées ; aucun secret n'est dans le code.
3. Dépôt > Settings > Secrets and variables > Actions > New repository secret : nom `NTFY_TOPIC`, valeur = ton nom de sujet.
4. Dépôt > Settings > Actions > General > Workflow permissions : « Read and write permissions », puis Save.
5. Onglet Actions > « JET Gold Intelligence » > Run workflow. Ouvre l'exécution pour vérifier le résumé, puis `state/rapport.md`.

## Réglages
- `MIN_STARS` (dans `gold.yml`) : 4 par défaut.
- `ASSETS` (dans `gold_bot.py`) : actifs liés et leur poids.
- `BULL` / `BEAR` : mots-clés de l'analyse des titres.
- Fréquence : toutes les 5 minutes de 6h à 20h UTC en semaine (soit 20h à 10h Tahiti), puis une fois par heure.

## Limites à connaître
- **Analyse des titres par mots-clés** : c'est un tri simple, qui peut se tromper sur l'ironie, les titres ambigus ou les nuances. Pour une vraie compréhension du texte, il faudrait brancher un modèle de langage via une clé API payante.
- **Pas de résultat chiffré des annonces** : le calendrier gratuit donne l'heure, la prévision et le précédent, pas le chiffre publié. La réaction se voit via les prix et les titres qui suivent.
- **GitHub retarde parfois les tâches planifiées** de 5 à 15 minutes aux heures chargées : un scalp M5 peut arriver en retard. Vérifie toujours que le prix est encore proche de la zone avant d'entrer.
- Le marché de l'or fermé (week-end) : le bot détecte les données périmées et n'envoie pas d'alerte de trade.
- Je n'ai pas pu tester les téléchargements réels (Yahoo, flux RSS) depuis mon environnement, seulement la logique sur données simulées. Au premier lancement, si un actif ou un flux ne répond pas, il est simplement ignoré. Envoie-moi le journal de l'exécution en cas d'erreur.
- Rien ne prouve que ces règles gagnent sur la durée. Teste en notant chaque alerte (résultat, glissement) pendant quelques semaines avant de miser gros, surtout sur ton compte réel.
