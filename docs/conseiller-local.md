# Conseiller local et préparation à la production

Le conseiller est un service Docker Compose optionnel. Il consulte Prometheus et Loki sur le réseau de la stack, avec des requêtes agrégées et bornées. Il propose des pistes de diagnostic avec les mesures qui les motivent et les vérifications à effectuer. Les décisions et l’application des changements restent à la charge de l’opérateur.

## Démarrer et lire le rapport

Après avoir démarré la [stack principale](../README.md), exécutez les commandes suivantes depuis la racine du dépôt.

```powershell
docker compose --profile advisor up -d --build --wait advisor
docker compose --profile advisor exec -T advisor python /app/main.py --show-latest
docker compose --profile advisor logs --tail 30 advisor
```

Le service consulte Prometheus et Loki via un réseau Docker interne dédié. Il tourne avec un utilisateur non privilégié, un système de fichiers racine en lecture seule et un volume nommé pour `/reports/latest.json`. À chaque cycle, le conteneur écrit un résumé JSON court dans ses journaux. Comparez l’heure du rapport et la période analysée à l’incident que vous examinez.

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ADVISOR_INTERVAL_SECONDS` | 300 | Intervalle entre les cycles du conseiller, en secondes. |
| `ADVISOR_WINDOW_MINUTES` | 30 | Étendue des requêtes rétrospectives, en minutes. |

Pour étendre la fenêtre d’observation, définissez par exemple `$env:ADVISOR_WINDOW_MINUTES = '60'` dans PowerShell, puis relancez la commande `up` ci-dessus pour appliquer le réglage au conteneur. Une fenêtre plus longue peut mélanger des phases d’activité distinctes. Une fenêtre plus courte amplifie les variations quand Codex traite peu de requêtes.

Pour arrêter uniquement le conseiller et garder son dernier rapport, exécutez `docker compose --profile advisor stop advisor`. `Stop.ps1` arrête aussi le conseiller avec la stack. [Profils Docker Compose](https://docs.docker.com/compose/how-tos/profiles/) · [Réseaux Compose internes](https://docs.docker.com/reference/compose-file/networks/#internal)

## Interpréter les propositions

Une proposition lie un constat à une preuve agrégée et à des vérifications. Elle sert à orienter l’analyse. Par exemple, un volume d’échecs ou de retentatives en hausse justifie de vérifier le statut et le transport dans le [dashboard Requêtes](http://127.0.0.1:3000/d/codex-requests). Un échantillon ancien du collecteur conduit à ouvrir la [vue d’ensemble](http://127.0.0.1:3000/d/codex-overview) et à vérifier si le collecteur Windows publie encore.

Chaque proposition contient un identifiant stable, une gravité (`critical`, `warning` ou `info`), un titre, des preuves numériques et des champs `action` et `verification`. Les familles de règles couvrent la disponibilité des cibles, la fraîcheur Windows, les rejets OTLP, les retentatives et échecs HTTP, les envois WebSocket avec contexte complet, la couverture des compteurs de tokens, la part d’entrée en cache, les échecs d’outils et la latence du tour. Une proposition sur un signal indisponible indique une limite de l’analyse.

Par exemple, l’agent signale les échecs HTTP à partir de 20 tentatives, 3 échecs et 10 % d’échecs. Trois retentatives suffisent à produire une piste de diagnostic distincte. Les règles sont explicites dans [le moteur](../advisor/engine.py). Pour mesurer l’effet d’une modification de prompt ou d’outil, utilisez le [rapport de tokens par tâche réussie](reseau-et-requetes.md#comparer-les-tokens-réellement-attribuables-aux-résultats) sur des tâches comparables.

Vérifiez la fraîcheur des mesures et le nombre d’observations avant d’interpréter les valeurs. Une source indisponible ou silencieuse limite le diagnostic. Une hausse de latence peut venir de l’inférence, du transport, d’un outil ou de la composition des tâches observées. Utilisez la proposition pour vérifier les hypothèses avec les vues et traces disponibles. [Catalogue des mesures](mesures.md) · [Réseau et requêtes](reseau-et-requetes.md)

Le conseiller utilise des règles locales. Il ne récupère pas les lignes brutes de Loki, n’envoie pas d’informations à un modèle externe et ne modifie ni le code ni la configuration du poste. Il ne redémarre aucun service. Le rapport contient des agrégats d’exploitation et reste dans l’environnement local. [Confidentialité et CI](ci-et-confidentialite.md)

## Appliquer une remédiation avec revue

1. Reproduisez le constat dans Grafana sur la même fenêtre. Confirmez que les cibles répondent et que la mesure correspond au modèle, au transport et à la charge concernés.
2. Identifiez un changement précis et définissez le résultat attendu. Par exemple, pour un collecteur arrêté, contrôlez la fraîcheur et le retour des séries après relance. Pour modifier une règle ou un seuil, comparez des fenêtres de charge et des effectifs comparables.
3. Faites relire le changement et son retour arrière. Validez les changements de code ou de configuration dans la CI synthétique. Si la configuration déployée change, validez-la avec `docker compose config -q` depuis le dossier de déploiement.
4. Appliquez le changement dans une fenêtre adaptée, puis relancez `Test-Stack.ps1 -RequireHostMetrics` et comparez les signaux de suivi au résultat attendu. Si le changement dégrade la collecte ou le service, utilisez le retour arrière prévu. [Exploitation et mise à niveau](exploitation.md)

## Écarts pour une production partagée

La configuration actuelle vise un poste de confiance. L’état des services et les résultats de la CI décrivent l’installation testée sur ce poste et les scénarios synthétiques. Pour une instance partagée ou un engagement de disponibilité, dimensionnez le déploiement selon les contraintes ci-dessous.

| Sujet | État local | À traiter avant le passage en production |
| --- | --- | --- |
| Disponibilité | Un hôte et des services Compose à instance unique. Le script lance le collecteur Windows lors de l’installation. | Définir les objectifs de disponibilité, les responsabilités d’astreinte et le mode de redémarrage ou de bascule après une panne de l’hôte. Tester une panne réelle. |
| Restauration | Volumes locaux, rétention configurée et dernier rapport conservé dans un volume. | Définir le délai et la perte de données acceptables, automatiser les sauvegardes chiffrées selon le besoin et tester leur restauration. |
| Accès | Grafana et OTLP sont liés à la boucle locale. Les backends communiquent sur le réseau Compose. | Avant toute ouverture réseau, définir les identités et rôles, protéger les accès et l’ingestion par authentification et chiffrement, segmenter le réseau et organiser la rotation des secrets. |
| Alertes | Prometheus évalue les alertes locales présentées dans Grafana. Le conseiller écrit un rapport. | Relier les alertes utiles à un routage d’astreinte, vérifier le regroupement et faire un test de notification de bout en bout. |
| Capacité | La rétention protège en partie le stockage et la CI vérifie une charge synthétique. | Tester les volumes, la cardinalité et les files à la charge prévue. Définir des budgets de latence, de perte de télémétrie et de stockage, avec alertes et contrôles. |
| Changements | La CI inspecte la configuration et les images, et exerce le chemin synthétique. | Valider en préproduction, gérer les migrations et documenter le retour arrière par version. Garder une copie exploitable des configurations et tester la procédure. |

[Docker Compose en production](https://docs.docker.com/compose/how-tos/production/) · [Authentification de Loki](https://grafana.com/docs/loki/latest/operations/authentication/) · [Routage des alertes Prometheus](https://prometheus.io/docs/alerting/latest/alertmanager/)
