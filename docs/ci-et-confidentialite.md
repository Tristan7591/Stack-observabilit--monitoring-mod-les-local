# Intégration continue et confidentialité

## Ce qui appartient au dépôt

Le dépôt contient le code, les configurations de service, les tableaux de bord, les tests et des scénarios de test fictifs. Les données d’exécution sont séparées du clone. Cette séparation vaut pour les journaux, les traces, `host.prom`, l’état du collecteur, le fichier de réglages Codex et ses sauvegardes, le mot de passe Grafana, les volumes Docker et les fichiers de session. Ne copiez pas ces données dans un commit, une issue ou un journal CI.

Avant de committer, examinez la liste des fichiers et le diff indexé.

```powershell
git status --short
git diff --cached --name-only
git diff --cached --check
git diff --cached
```

N’ajoutez que les fichiers utiles. Les règles d’exclusion réduisent les erreurs de manipulation. Un fichier déjà suivi reste suivi malgré un ajout au fichier d’ignore, et un secret committé peut subsister dans l’historique. Si une valeur sensible a été publiée, révoquez-la ou faites-la tourner, puis traitez sa présence dans l’historique et les logs. [Suppression de données sensibles dans GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)

Le contrôle Git `pre-push` est actif dans la copie de travail initiale. Dans un autre clone, activez-le avec `git config --local core.hooksPath .githooks`. Il inspecte chaque commit envoyé, y compris ceux qui introduisent un secret supprimé dans un commit ultérieur.

## Trajet des données sur le poste

Le collecteur Windows produit des nombres et des libellés de processus ou de volumes. Il ne copie pas le contenu des fichiers qu’il mesure. Le collecteur OTLP remplace le corps brut des journaux et conserve une liste d’attributs de journal et de span utiles au diagnostic. Les attributs de métriques passent par un filtre de clés. Garder des noms d’outils, des modèles ou des identifiants de conversation et d’appel permet les corrélations locales. Ces métadonnées ont aussi besoin de rester sur le poste. Le filtrage n’est pas une anonymisation générale de producteurs OTLP arbitraires.

Le profil de la stack utilise des receveurs locaux et des volumes locaux. L’ingestion OTLP sur boucle locale ne nécessite pas de jeton à transmettre à GitHub. Les réglages de télémétrie du produit Codex et le trafic réseau nécessaire à une requête modèle relèvent de la configuration de Codex. Le routage documenté ici concerne uniquement les signaux traités par la stack.

## Exécution sur GitHub Actions

Le workflow [CI](../.github/workflows/ci.yml) utilise des runners Linux et Windows hébergés par GitHub. Il s’exécute lors des pull requests, des pushes sur `main`, sur déclenchement manuel et selon une planification hebdomadaire. Le job Linux génère le fichier de métriques Windows, le mot de passe de test et les requêtes OTLP. Il démarre une composition isolée, puis retire ses conteneurs et ses volumes temporaires. Le job Windows valide les relevés TCP avec des connexions synthétiques sur sa propre boucle locale. Aucun job ne configure Codex ou ne contacte le fournisseur de modèles. La variable `CODEX_OBSERVABILITY_RUNTIME_DIR` pointe pour les tests Docker vers un dossier temporaire du runner.

| Étape | Pourquoi |
| --- | --- |
| Validation de la liste des fichiers suivis, workflows, scripts, dashboards et configurations | Détecte les chemins imprévus, certaines signatures de données locales, les erreurs de syntaxe et les changements de politique réseau et de montage. Le pipeline valide aussi les configurations avec `promtool` et le CLI du Collector. |
| Scan statique | Trivy analyse une copie de l’arbre Git. Les découvertes de secrets et les mauvaises configurations de niveau HIGH ou CRITICAL bloquent le contrôle. Les journaux donnent des compteurs et des identifiants de règles. |
| Construction Buildx | Valide le Dockerfile et construit l’image Linux amd64 depuis `fixtures/`. Le réseau des étapes de build est désactivé et le cache réutilise les couches de construction. |
| Scan des images | Inspecte les dépendances du système et de l’application dans les images effectivement utilisées et dans l’image de test construite. |
| Test de bout en bout | Contrôle que les données synthétiques arrivent dans Prometheus, Loki et Tempo. Vérifie aussi le filtre sur des marqueurs synthétiques. |

Le workflow utilise `pull_request`, des permissions de lecture et des actions épinglées par SHA. Les identifiants du checkout ne sont pas laissés dans le dépôt de travail. Aucun registre ni artefact de données n’est publié par le pipeline. Le cache GitHub Actions vise les couches BuildKit de la seule image de test et sa mise en cache est limitée à la branche de confiance. Les logs de CI se limitent au code et à l’environnement de test synthétique. [Sécurisation de GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use) · [Gestion du cache Buildx](https://docs.docker.com/build/ci/github-actions/cache/)

## Images et analyse des vulnérabilités

La stack réutilise les images officielles, identifiées par leur version et leur digest. L’image de test épingle aussi sa base par digest et exécute un seul fichier Python sous un UID non root. La construction prend un contexte limité et n’a besoin ni de secrets de build ni de données du poste. Dependabot propose des mises à jour pour les actions, les images Compose et la base Dockerfile. Examinez les nouveautés et migrations avant de fusionner une mise à jour. [Bonnes pratiques Docker Build](https://docs.docker.com/build/building/best-practices/) · [Secrets à la construction](https://docs.docker.com/build/building/secrets/)

Le scan Trivy recherche les vulnérabilités connues à la date de sa base dans les six images Compose et dans l’image synthétique construite. Les niveaux HIGH et CRITICAL avec correctif disponible bloquent le contrôle. Les résultats de ces niveaux sans correctif restent visibles dans le rapport. Pour traiter une alerte, identifiez l’image, le composant, la sévérité et la version corrigée, puis relancez les contrôles avec l’image mise à jour. Un résultat vert est borné aux images contrôlées et à la base utilisée. [Analyse des vulnérabilités Trivy](https://trivy.dev/docs/latest/scanner/vulnerability/)
