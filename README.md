# Observabilité locale de Codex

Stack Windows + Docker Compose pour suivre les processus Codex, l’usage des modèles, les latences, les journaux et les traces. Grafana et le récepteur OpenTelemetry sont accessibles sur la boucle locale. Les données d’exécution sont stockées sur le poste. Les tests GitHub Actions utilisent leurs propres données synthétiques.

## Démarrer

Utilisez Windows, PowerShell, Docker Desktop avec le moteur de conteneurs Linux et Docker Compose v2. Exécutez les commandes depuis la racine du dépôt, sous le compte qui exécute Codex.

```powershell
.\Install.ps1
.\Test-Stack.ps1 -RequireHostMetrics
```

Connectez-vous à [Grafana](http://127.0.0.1:3000) avec l’identifiant `admin`. Le mot de passe est généré à la première installation et stocké dans `%LOCALAPPDATA%\CodexObservability\grafana-admin-password.txt`. Gardez ce fichier sur le poste. Après avoir enregistré les tâches en cours, relancez Codex Desktop ou ouvrez un nouveau processus CLI pour charger la configuration OpenTelemetry. Lancez un nouveau tour pour alimenter les métriques d’usage.

| Vue | Utilisation |
| --- | --- |
| [Vue d’ensemble](http://127.0.0.1:3000/d/codex-overview) | Santé de la stack, fraîcheur du collecteur et alertes locales. |
| [Processus et caches Windows](http://127.0.0.1:3000/d/codex-windows) | CPU, mémoire, threads, handles, entrées-sorties et caches par famille. |
| [Usage et latences Codex](http://127.0.0.1:3000/d/codex-usage) | Tokens, cache de prompts, latences, outils, requêtes et état des services. |
| [Réseau Windows et sonde fournisseur](http://127.0.0.1:3000/d/codex-network) | Connexions TCP par état et par PID, mesures DNS, TCP et TLS de la sonde optionnelle. |
| [Requêtes Codex](http://127.0.0.1:3000/d/codex-requests) | Tentatives HTTP, envois WebSocket, replis de transport et liens vers les traces. |
| [Évaluation synthétique](http://127.0.0.1:3000/d/codex-evaluation) | Résultat et durée de scénarios fictifs, séparés de la télémétrie Codex. |
| Journaux et traces | Accédez à Loki et Tempo via les liens du dashboard **Vue d’ensemble** ou via **Explore** dans Grafana. |

## Documentation

- [Architecture, ports et conservation](docs/architecture.md)
- [Catalogue et interprétation des métriques](docs/mesures.md)
- [Réseau, requêtes fournisseur et rapport de consommation](docs/reseau-et-requetes.md)
- [Exploitation, mise à niveau et dépannage](docs/exploitation.md)
- [Conseiller local, remédiation et préparation à la production](docs/conseiller-local.md)
- [Scénarios d’évaluation et critères de réussite](docs/evaluation.md)
- [GitHub Actions, images Docker et confidentialité](docs/ci-et-confidentialite.md)

## Arrêter ou reprendre

```powershell
.\Stop.ps1
.\Install.ps1
```

`Stop.ps1 -RemoveContainers` retire les conteneurs et le réseau Compose en préservant les volumes nommés. `Enable-CodexTelemetry.ps1 -Disable` enlève le bloc de configuration OTLP géré par l’installation. Un redémarrage du processus Codex applique ce changement.

## Configurer un exporteur OTLP existant

Le script installe un bloc géré dans le fichier Codex du compte courant. Une section `[otel]` personnalisée demande une fusion manuelle. Adaptez les valeurs suivantes dans la section existante, ou démarrez simplement l’infrastructure avec `Install.ps1 -SkipCodexTelemetry`. [Référence Codex](https://learn.chatgpt.com/docs/config-file/config-reference)

```toml
[otel]
environment = "local"
log_user_prompt = false
log_agent_responses = false
log_guardian_assessments = false
log_sse_deltas = false
exporter = { otlp-http = { endpoint = "http://127.0.0.1:4318/v1/logs", protocol = "binary" } }
trace_exporter = { otlp-http = { endpoint = "http://127.0.0.1:4318/v1/traces", protocol = "binary" } }
metrics_exporter = { otlp-http = { endpoint = "http://127.0.0.1:4318/v1/metrics", protocol = "binary" } }

[otel.tool_result]
max_bytes = 0
```
