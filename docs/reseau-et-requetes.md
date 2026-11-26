# Réseau et requêtes fournisseur

La stack propose deux vues complémentaires. [Réseau Windows](http://127.0.0.1:3000/d/codex-network) décrit les sockets visibles sur le poste et les mesures d’une sonde externe à la demande. [Requêtes Codex](http://127.0.0.1:3000/d/codex-requests) utilise les événements et traces que Codex émet au niveau applicatif. Une connexion TCP peut servir plusieurs requêtes, par exemple avec un flux WebSocket persistant.

## Connexions observées sur Windows

| Métrique Prometheus | Lecture |
| --- | --- |
| `codex_host_tcp_snapshot_success{family}` | 1 si le relevé de la table TCP a réussi pour cette famille, 0 s’il a échoué. Permet d’interpréter une série manquante. |
| `codex_host_tcp_connections{family,state,category}` | Nombre de sockets TCP des processus rattachés à Codex, y compris les sockets en écoute, agrégé par famille IPv4 ou IPv6, état et catégorie. |
| `codex_host_process_tcp_connections{process,pid,parent_pid,family,state,category}` | Même comptage pour les PID rattachés à Codex. Le label de processus reste local. |

La catégorie `loopback` correspond à la boucle locale. `tls_port` regroupe les sockets dont le port local ou distant est 443 ou 8443, après exclusion de la boucle locale. `other` couvre le reste. Ce classement par port ne permet pas de confirmer TLS ni d’identifier un fournisseur. Le label `state` permet par exemple de distinguer `listen`, `syn_sent`, `established` et `time_wait`.

Le relevé publie des compteurs instantanés sous forme de jauges et ne conserve ni adresse IP, ni corps, ni paquet. Une socket ouverte et fermée entre deux relevés peut passer inaperçue. La table TCP ne couvre pas UDP ou QUIC et ne donne ni débit ni volume d’octets. Pour analyser une requête, passez au dashboard applicatif. [Tables TCP et PID sous Windows](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable)

## Requêtes natives dans Codex

- **HTTP** — comptez les tentatives instrumentées, les statuts, les échecs explicites et les durées client. Les événements natifs `codex.api_request` portent un index `attempt` à partir de zéro. Un index supérieur à zéro représente une retentative observée. Une erreur avant réponse peut ne fournir aucun statut.
- **WebSocket** — distinguez les ouvertures, les envois, les échecs d’envoi, les événements du flux et les replis vers HTTP. Un envoi réussi indique que le transport a accepté l’envoi. La durée d’envoi ne mesure pas toute la génération.
- **Contexte et usage** — le mode WebSocket `full` renvoie du contexte complet. La première requête et certains changements de session peuvent justifier ce mode. Un envoi `full`, un repli HTTP ou une réauthentification ne prouvent pas qu’un même appel a été facturé plusieurs fois. Les mesures de tokens et, lorsqu’il est présent, `ttft_ms` proviennent d’événements d’achèvement et se lisent séparément.
- **Détail d’exécution** — ouvrez les spans de requêtes dans Tempo, puis les journaux dans Loki. Le champ facultatif `auth.request_id` apparaît sur certains événements d’erreur ou de négociation. Les événements d’achèvement qui portent l’usage n’exposent pas ce même champ de façon jointe. Dans les métadonnées structurées de Loki, les points d’un nom de champ sont normalisés en traits de soulignement, d’où `auth_request_id`. Utilisez le contexte de trace lorsqu’il est disponible. Une proximité temporelle ou un identifiant sur un seul événement ne suffit pas à attribuer des tokens à une tentative. [Télémétrie Codex](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry) · [Métadonnées OTLP dans Loki](https://grafana.com/docs/loki/latest/send-data/otel/)

Commencez par la période et le modèle concernés. Examinez le volume avant les percentiles, puis comparez le taux d’échec, le délai d’ouverture et le délai d’envoi. Une série absente peut indiquer un transport non utilisé, un champ facultatif, un filtrage ou un faible nombre d’observations. La latence jusqu’au premier octet HTTP, celle jusqu’au premier événement de sortie du flux et celle jusqu’au premier texte visible mesurent trois étapes distinctes. Le champ `ttft_ms` de Codex peut correspondre au premier événement d’outil ou à un autre élément de sortie. [Implémentation du flux Codex](https://github.com/openai/codex/blob/rust-v0.171.0-alpha.13.2/codex-rs/core/src/client.rs#L2913)

## Sonder le trajet réseau depuis le poste

La commande suivante nécessite Python et curl. À chaque lancement, elle émet une requête HTTPS `HEAD` sans authentification vers `https://api.openai.com/v1/models`, puis envoie les mesures et un span au Collector local sur `127.0.0.1:4318`. Un statut `401` est attendu lorsque le serveur refuse l’accès anonyme. Il reste utile pour constater que le trajet a atteint une réponse HTTP. La sonde n’exécute aucun modèle et n’envoie ni prompt ni clé au fournisseur.

```powershell
python scripts/provider_probe.py --emit-otlp
```

La commande affiche son résultat en JSON. Avec `--emit-otlp`, ouvrez le dashboard Réseau ou recherchez le `trace_id` affiché dans **Explore → Tempo**. Sans cette option, seul le JSON est affiché. `--self-test` vérifie la conversion et les données synthétiques hors ligne.

| Phase dans `codex_provider_probe_duration_seconds` | Lecture |
| --- | --- |
| `name_lookup` | Temps cumulé jusqu’à la résolution du nom, si mesurable. |
| `tcp_connect` | Temps entre la résolution et la fin de la connexion TCP. |
| `tls_handshake` | Temps entre la connexion TCP et la fin de la négociation TLS. |
| `time_to_first_byte` | Temps cumulé du début du transfert au premier octet reçu, incluant les étapes précédentes et l’attente du serveur. |
| `total` | Durée cumulée de la sonde. |

Les jauges de statut HTTP, de code de sortie curl et d’horodatage du dernier échantillon aident à distinguer l’absence de mesure d’un problème de transport. L’exporteur maintient le dernier point de chaque phase pendant une heure. Le dashboard applique `codex_provider_probe_phase_available` pour masquer une phase absente de la sonde la plus récente. Utilisez le JSON ou la trace pour lire une seule exécution. Les options réseau de l’environnement, notamment un éventuel proxy, peuvent modifier le trajet mesuré. La sonde utilise la validation TLS de curl et reflète son propre trajet réseau. Elle ne mesure ni le trafic d’un tour Codex ni son délai avant le premier token. [Variables de chronométrage curl](https://curl.se/docs/manpage.html#-w)

## Instrumenter un client Responses dont vous contrôlez le code

1. Faites porter à chaque tâche un identifiant opaque local. Attribuez un identifiant stable à chaque opération logique et un numéro distinct à chaque tentative réellement envoyée. Ouvrez un span de tâche et un span enfant par tentative. Si vous devez mesurer chaque tentative du SDK, instrumentez le transport ou gérez les reprises explicitement. Une absence de réponse peut laisser l’usage inconnu.
2. Au niveau HTTP, envoyez un `X-Client-Request-Id` ASCII unique par requête. Le filtre peut conserver les chaînes bornées `openai.client.request.id`, `openai.request.id` et `gen_ai.response.id` pour l’identifiant envoyé par le client, le `x-request-id` renvoyé par OpenAI et l’identifiant de réponse. Ajoutez le modèle, le statut, la durée et une catégorie d’erreur. L’en-tête `openai-processing-ms`, lorsqu’il est présent, fournit un repère de traitement côté API. Utilisez des attributs de trace pour ces identifiants à forte cardinalité, sans les ajouter aux labels des métriques. Gardez les clés complètes utilisées pour le regroupement par tâche et opération dans le JSONL local expurgé. [Diagnostic des requêtes OpenAI](https://developers.openai.com/api/reference/overview#debugging-requests)
3. En mode streaming, démarrez un chronomètre monotone juste avant l’envoi. Mesurez séparément le premier octet à la réception HTTP et l’arrivée du premier delta de texte non vide, un repère client pour le premier texte visible. Un delta SSE peut porter plusieurs tokens, et une réponse centrée sur les outils peut ne produire aucun texte. À l’achèvement, relevez `usage.input_tokens`, `usage.output_tokens` et `usage.input_tokens_details.cached_tokens` quand ils sont présents. Gardez une valeur absente sous forme de `null`. [Événements streaming](https://developers.openai.com/api/docs/guides/streaming-responses) · [Référence Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)
4. Exportez une liste explicite de métadonnées. Laissez hors télémétrie les prompts, réponses brutes, arguments ou sorties d’outils, URL complètes, cookies et en-têtes d’autorisation. Utilisez les conventions `gen_ai.*` lorsque proposées et vérifiez les attributs retenus par le filtre du Collector. Cette instrumentation se place dans le code du client et ne requiert ni interception TLS ni certificat de substitution. [Conventions GenAI OpenTelemetry](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

## Comparer les tokens réellement attribuables aux résultats

Le script `provider_report.py` traite uniquement un JSONL déjà expurgé, produit par le client que vous instrumentez. Il n’interroge ni Codex ni le fournisseur. `--example` affiche des événements fictifs sur la sortie standard, `--self-test` valide le calcul hors ligne et `--input` lit le fichier explicitement indiqué. Gardez ce fichier hors du clone.

```powershell
python scripts/provider_report.py --example
python scripts/provider_report.py --self-test
python scripts/provider_report.py --input "$env:LOCALAPPDATA\CodexObservability\provider-attempts.jsonl"
```

| Type de ligne | Champs attendus |
| --- | --- |
| `kind = "attempt"` | `task_id`, `operation_id`, `attempt` numéroté à partir de 1 pour cette opération, `outcome` parmi `success`, `failed` ou `cancelled`, `input_tokens`, `output_tokens` et `cached_input_tokens`. Les nombres de tokens inconnus valent `null`. |
| `kind = "task"` | `task_id`, `variant`, `passed` issu d’une vérification externe, et `attempts_complete` qui confirme si toutes les tentatives ont été consignées. |

Le rapport calcule notamment le taux de réussite par variante, les tokens connus des tentatives échouées ou annulées, les tokens d’entrée connus des tentatives suivantes et le ratio de cache pour les tentatives qui le déclarent. Le total de tokens par tâche réussie inclut les tentatives enregistrées sur les tâches réussies et échouées de la même variante. Il est publié uniquement lorsque la comptabilité est complète et qu’au moins une tâche a réussi. Une séquence numérotée sans trou ne prouve pas à elle seule que le journal est complet. Ces agrégats d’usage ne se substituent pas aux données de facturation.
