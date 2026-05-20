# Gestionnaire de stocks des ventes organiques

Application web développée en Python avec Flask, destinée aux boutiques Shopify utilisant Google Analytics GA4.

Elle permet de prévoir les stocks nécessaires à faire pour satisfaire les ventes SEO / organiques, plus stables et plus faciles à anticiper que les ventes issues de publicités.

L’objectif est de limiter les ruptures de stock tout en évitant de commander trop de produits dormants.

Le logiciel fonctionne avec trois imports CSV :

- un export Shopify des produits ;
- un export Shopify des stocks ;
- un export GA4 des ventes organiques.

Les exports de stocks et de ventes doivent être mis à jour régulièrement afin de garder des prévisions fiables.


## Fonctionnement

L’application permet de :

- consulter les stocks disponibles pour les produits, par couleur et par taille ;
- estimer les jours avant rupture ;
- calculer les quantités à commander ;
- générer un rapport CSV de réapprovisionnement.

Le réapprovisionnement se fait un lundi sur deux. L’utilisateur peut télécharger un rapport indiquant le nombre d’unités à commander pour chaque article.

## Algorithme de prévision

Pour chaque article, l’algorithme estime les jours avant rupture et les quantités à commander à partir de :

- la moyenne des ventes sur les 21 derniers jours ;
- un coefficient saisonnier calculé avec le ratio ventes futures N-1 / ventes récentes N-1 ;
- le stock actuel ;
- un délai de réapprovisionnement de 14 jours ;
- une marge de sécurité de 10 %.


## Améliorations possibles pour une V2

- Gérer plus proprement les ruptures de stock avant la prochaine date de commande, avec une action exceptionnelle possible si un article arrive à court.

- Suivre les estimations incorrectes : commandes trop faibles ayant mené à une rupture, ou commandes trop élevées ayant créé trop de stock, afin d’optimiser l’ajustement de l’algorithme.

- Lorsqu’un article apparaît dans un export de ventes ou de stocks, mais pas encore dans la base produits du logiciel, avoir l’option de l’associer à un article de la base si son nom a été mis à jour.

## Lancer le projet

Depuis le dossier du projet :

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py


http://127.0.0.1:5050

