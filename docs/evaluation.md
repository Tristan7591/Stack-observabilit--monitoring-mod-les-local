# Évaluation locale

La télémétrie native de Codex mesure l’exécution. Pour mesurer la réussite d’une tâche, il faut aussi définir un résultat attendu, observer le résultat obtenu et le vérifier. Les scénarios synthétiques livrés ici entraînent et vérifient cette chaîne sans exécuter Codex ni interroger un modèle externe.

## Exécuter la suite synthétique

Exécutez cette commande depuis la racine du dépôt avec Python.

```powershell
python fixtures/fixture_runner.py self-test
```

Deux tâches sur la normalisation de libellés et l’agrégation numérique illustrent chacune trois variantes. La référence doit réussir, une régression fonctionnelle doit échouer et un faux changement hors périmètre doit faire échouer une variante fonctionnellement correcte. Les fonctions et la liste simulée des fichiers modifiés proviennent du code du test. Seule la durée des checks est mesurée. Elle ne représente pas la latence d’un modèle.

Pour tester aussi le passage des métriques synthétiques vers la stack locale en cours d’exécution, lancez l’émission locale.

```powershell
python fixtures/fixture_runner.py emit --endpoint http://127.0.0.1:4318
```

L’émission utilise le service `codex-fixture` et accepte seulement la boucle locale ou `otel-collector:4318` dans Compose. Consultez le [dashboard d’évaluation synthétique](http://127.0.0.1:3000/d/codex-evaluation). Le test de bout en bout général reste disponible via `Test-Stack.ps1`.

| Jauge Prometheus, par tâche et variante | Lecture |
| --- | --- |
| `codex_fixture_attempt_success` | 1 si tous les checks passent et si le périmètre est respecté, 0 sinon. |
| `codex_fixture_check_pass_ratio` | Part des checks fonctionnels réussis. Elle peut valoir 1 même si la variante est hors périmètre. |
| `codex_fixture_checks` | Nombre de cas exécutés, dénominateur du ratio fonctionnel. |
| `codex_fixture_scope_violations` | Nombre de chemins fictifs hors périmètre. |
| `codex_fixture_runtime_seconds` | Secondes écoulées lors des checks, mesurées par une horloge monotone. |

L’image exécute la même suite par défaut, sans réseau ni montage.

```powershell
docker buildx build --load --tag codex-fixture:local ./fixtures
docker run --rm --network none --read-only codex-fixture:local
```

## Concevoir de vraies évaluations

Commencez par des tâches reproductibles, chacune associée à des entrées, à un environnement de départ, à des critères d’acceptation et à un budget. Une tâche de correction de bug peut exiger qu’un test initialement en échec passe, que les tests existants restent verts et que les modifications restent dans le périmètre prévu.

| Métrique | Choix et lecture |
| --- | --- |
| Réussite à la première tentative | Part des premières tentatives répondant à tous les critères obligatoires. Mesure le travail qui aboutit sans deuxième passe. |
| Fiabilité par scénario | Part des répétitions réussies sous le même budget et avec un environnement équivalent. Nombre d’essais à afficher avec le résultat. |
| Respect du périmètre et des actions | Compte les écritures hors périmètre, les actions bloquées et les interventions nécessaires. Distingue ces écarts des simples erreurs techniques. |
| Durée et travail par réussite | Compare le temps, les tokens et les appels consommés pour produire un résultat accepté. Précisez si les tentatives échouées sont incluses. |
| Exécution par difficulté | Permet de comparer des tâches comparables. La difficulté peut être décrite par catégorie, complexité du changement ou effort humain de référence. |

Privilégiez les critères déterministes pour les résultats testables. Ajoutez une grille explicite et une validation humaine pour les critères qualitatifs. Gardez des essais reproductibles et distinguez le succès du processus, le résultat de la tâche et le respect des règles. [Anthropic — évaluation d’agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) · [OpenAI — notation de traces](https://developers.openai.com/api/docs/guides/trace-grading) · [METR — horizon de réussite](https://metr.org/time-horizons/)

## Extensions possibles

Langfuse héberge les scores, jeux d’évaluation et comparaisons d’expériences. Une intégration exploite les traces et attributs effectivement émis. Une trace filtrée peut être annotée, avec une capacité d’évaluation limitée aux données conservées. LangGraph enregistre l’état et les checkpoints des agents construits avec son runtime. Ces checkpoints permettent de reprendre ou bifurquer une exécution instrumentée. Les composants de la stack actuelle restent utiles pour l’état du poste, les alertes et les traces OpenTelemetry. [Évaluation Langfuse](https://langfuse.com/docs/evaluation/overview) · [Persistance LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence)
