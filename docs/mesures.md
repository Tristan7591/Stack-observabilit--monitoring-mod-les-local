# Catalogue des mesures

Une **jauge** représente un état instantané. Un **compteur** s’accumule et peut repartir à zéro au redémarrage. Utilisez `rate` pour un débit ou `increase` pour une variation sur la période. Un **histogramme** expose un nombre de valeurs, une somme et des classes de mesure. Les p50, p95 et p99 calculés sur les classes sont des estimations et gagnent à être lus avec le nombre d’observations. [Histogrammes Prometheus](https://prometheus.io/docs/practices/histograms/)

## Poste Windows

La colonne **Signal** donne le suffixe des métriques `codex_host_…`. Les séries par processus portent le nom, le PID et le PID parent.

| Signal | Unité et type | Choix et interprétation |
| --- | --- | --- |
| `collector_last_success_timestamp_seconds` | Secondes Unix, jauge | Âge du dernier relevé en retranchant la valeur à l’heure actuelle. Vérifie la collecte Windows même lorsque Node Exporter répond. |
| `process_count` | Processus, jauge | Taille de la famille visible au moment du relevé. Une chute peut correspondre à la fermeture de Codex. |
| `process_cpu_usage_percent` | Pourcentage d’un cœur logique, jauge | Détecte les pics par PID. Une valeur de 100 correspond à un cœur occupé. Un processus parallèle peut dépasser 100. Les fenêtres de relevé lissent les charges très courtes. |
| `process_cpu_seconds_total` | Secondes CPU cumulées, compteur | Mesure l’effort CPU sur une période en tenant compte des redémarrages de compteurs. Complète le pic instantané. |
| `process_working_set_bytes` | Octets, jauge | Mémoire physique résidente à l’instant du relevé. Une somme sur plusieurs PID peut compter plusieurs fois les pages partagées. |
| `process_private_bytes` | Octets, jauge | Mémoire virtuelle privée engagée par le processus. Sa croissance à charge constante peut signaler une rétention mémoire. |
| `process_threads`, `process_handles` | Nombre, jauges | Diagnostiquent une explosion de concurrence ou d’objets Windows ouverts, à comparer à la charge et à l’activité des outils. |
| `process_io_read_bytes_total`, `process_io_write_bytes_total` | Octets cumulés, compteurs | `rate` donne des octets/s. Les compteurs d’E/S Windows agrègent les E/S comptabilisées pour le processus. Ils ne représentent pas directement l’activité d’un disque physique particulier. |
| `cpu_usage_percent`, `logical_processors` | % de l’hôte et nombre, jauges | Repère la saturation globale, et donne le dénominateur pour rapporter une occupation en équivalents-cœurs à la machine entière. |
| `memory_total_bytes`, `memory_available_bytes` | Octets, jauges | Le rapport disponible/total donne une mesure de marge mémoire. |
| `volume_size_bytes`, `volume_free_bytes` | Octets, jauges | Le rapport libre/taille signale les volumes qui risquent d’être saturés, qu’ils hébergent Codex ou d’autres fichiers. |
| `cache_bytes`, `cache_files` | Octets et nombre, jauges | Taille et fragmentation approximative des caches retenus par catégorie. Les catégories peuvent se recouvrir. |
| `cache_scan_success`, `cache_last_scan_timestamp_seconds` | 0/1 par catégorie et secondes Unix de la dernière tentative, jauges | Distinguent un cache vide, un scan qui a échoué et un relevé ancien. Les tailles peuvent conserver la dernière mesure réussie. Une absence de donnée n’est pas un zéro. |

Les catégories de cache observées couvrent le cache Codex local, la description des modèles en cache, le cache des plugins, les runtimes de l’application, les téléchargements de runtime, certains caches du navigateur et ceux de l’application empaquetée. Le collecteur inspecte les entrées et leurs tailles dans une liste de répertoires dédiée. Ces octets sur disque sont distincts du cache de tokens côté modèle.

## Usage et latence des modèles

Les métriques natives sont relevées sur le job `codex-otlp`. Dans les courbes de durée, selon la traduction OpenTelemetry → Prometheus, une famille déclarée en millisecondes peut recevoir un suffixe supplémentaire `_milliseconds`. Les panneaux acceptent les variantes prévues.

| Famille ou panneau | Unité et lecture | Pourquoi la mesurer |
| --- | --- | --- |
| `codex_turn_token_usage` | Tokens par type, histogramme. La somme donne le volume, le nombre donne le nombre de valeurs pour le type choisi. | Relie la taille du contexte et du travail produit à la consommation. L’entrée en cache est une partie de l’entrée totale. Les types de tokens peuvent se recouvrir, donc utilisez la série `total` pour un total. |
| Part des tokens d’entrée servis par le cache | Tokens d’entrée en cache divisés par les tokens d’entrée mesurés sur le même périmètre | Estime la réutilisation du contexte. Un dénominateur absent ou nul donne une donnée indisponible. Ce ratio ne fournit ni les octets sur disque ni un coût facturé. |
| Débit agrégé de tokens | Tokens par seconde de temps écoulé, agrégés sur la fenêtre | Visualise une charge en bursts et la tendance d’usage. Le débit de décodage d’une réponse unique demande la durée de génération correspondante. |
| `codex_turn_e2e_duration_ms` | Durée d’un tour, histogramme en ms | Couvre le temps vécu à l’échelle d’un tour. La durée est influencée par plusieurs opérations et par le type de tâche. |
| `codex_turn_ttft_duration_ms` | Temps jusqu’au premier token, histogramme en ms | Mesure la réactivité du démarrage d’un tour. Plusieurs échanges peuvent intervenir dans le délai. |
| Effectifs des histogrammes de latence | Plusieurs nombres sur la fenêtre, selon la famille | Donne le volume à côté des percentiles. Un tour peut contribuer à la latence totale sans fournir un délai de premier token. |
| `codex_responses_api_engine_service_tbt_duration_ms` | Intervalles entre tokens observés par le moteur, en ms | Décrit le rythme de décodage rapporté par un trajet instrumenté. Sa couverture dépend du service et du transport. |
| `codex_api_request_duration_ms`, `codex_websocket_request_duration_ms`, `codex_tool_call_duration_ms` | p95 du temps HTTP, WebSocket et outils sur leurs observations respectives, en ms | Localise un ralentissement. Le tableau par outil montre jusqu’à huit outils classés par p95. |
| `codex_responses_api_inference_time_duration_ms`, `codex_responses_api_overhead_duration_ms` | p95 d’inférence et de surcoût distincts, en ms | Sépare les signaux rapportés par la Responses API. Les durées des différents signaux peuvent se recouvrir. |
| `codex_turn_critical_path_component_ms` | Temps moyen par composante du chemin critique, en ms | Rapporte l’inférence avec compaction, les outils, la revue automatique, l’attente utilisateur, le surcoût et la durée inconnue sur les captures complètes qui l’exposent. |
| `codex_api_request_total`, `codex_websocket_request_total` | Nombre, débit et ventilation par statut ou résultat si émis | Sépare la charge du transport des erreurs. Les ratios d’échec utilisent les observations avec un résultat explicite, par famille. |
| `codex_websocket_connect_total`, `codex_transport_fallback_to_http_total` | Ouvertures WebSocket et replis vers HTTP, compteurs | Mesure les changements de transport. Les ouvertures comprennent les premières connexions et ne sont pas un compteur de reconnexions à elles seules. |
| `codex_tool_call_total`, `codex_mcp_call_total` | Appels, débit et résultats si émis | Détecte un outil instable et un volume inattendu. Les statuts MCP peuvent inclure des opérations bloquées sans nom d’outil. Un appel réussi au niveau protocolaire ne détermine pas l’issue de la tâche utilisateur. |
| `codex_mcp_tools_list_duration_ms_count` | Nombre de mesures, ventilées par label `cache` | Mesure la réutilisation des métadonnées d’outils. Distinct du cache de contexte et des octets de cache Windows. |
| `codex_approval_requested_total` | Nombre de demandes et ventilation par décision | Met en évidence la charge des approbations. Un refus n’est pas automatiquement une erreur du modèle. |

Segmentez par modèle et comparez des fenêtres avec un volume d’observations et une charge comparables. La fenêtre glissante des latences vaut 15 minutes par défaut. Elle est réglable à 5, 15, 30 ou 60 minutes. Les panneaux de distribution par modèle requièrent au moins une observation pour le p50, 20 pour le p95 et 100 pour le p99. Ces seuils évitent les fenêtres presque vides et ne garantissent pas la stabilité statistique. Une série absente peut correspondre à un signal facultatif, à un modèle filtré ou à un effectif insuffisant. [Catalogue des métriques natives de Codex](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry) · [Calcul du chemin critique dans Codex](https://github.com/openai/codex/blob/main/codex-rs/core/src/turn_timing/report.rs)

## Traces et santé de la stack

| Signal | Utilité |
| --- | --- |
| `up` | Disponibilité du point de relevé Prometheus. Complétez avec la fraîcheur de la source et une requête de bout en bout. |
| `node_textfile_scrape_error` | Détecte la lecture ou le parsing d’un fichier d’exposition invalide. |
| `traces_spanmetrics_calls_total` | Débit des spans par service et codes de statut observés. Un même tour peut produire plusieurs spans. |
| Distribution de latences dérivée des spans | Aide à comparer les durées par service et explorer la trace correspondante. Les fenêtres et la couverture du générateur diffèrent de celles de l’histogramme natif des tours. |
| Métriques internes d’OpenTelemetry Collector | Relevé des volumes reçus et exportés, rejets et erreurs pour identifier une perte de télémétrie. |

Les traces gardent les identifiants nécessaires aux liens trace ↔ journal. Ces identifiants sont réservés au détail d’exécution et restent en local. L’ajout de valeurs très variables telles qu’un identifiant par session ou appel aux séries Prometheus multiplie leur cardinalité. [Instrumenter sans cardinalité excessive](https://prometheus.io/docs/practices/instrumentation/#do-not-overuse-labels) · [Générateur de métriques Tempo](https://grafana.com/docs/tempo/latest/metrics-from-traces/metrics-generator/)
