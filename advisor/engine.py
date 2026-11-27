"""Derive local, evidence-based advice from an already aggregated snapshot."""

from collections.abc import Mapping
import math


SIGNALS = (
    "targets_down", "host_sample_age_seconds", "collector_rejected",
    "http_attempts", "http_retries", "http_failures", "ws_sends",
    "ws_full_resends", "completion_events", "input_tokens", "cached_input_tokens",
    "failed_tool_calls", "tool_calls", "turn_count", "turn_p95_seconds",
)
MAX_MEASUREMENT = 10**15
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _measurement(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if not 0 <= value <= MAX_MEASUREMENT or not math.isfinite(value):
        return None
    return float(value)


def _share(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is not None and denominator and numerator <= denominator:
        return numerator / denominator
    return None


def recommend(snapshot: Mapping[str, object]) -> list[dict]:
    """Return severity-ordered advice. Missing measurements remain unknown."""
    values = {key: _measurement(snapshot.get(key)) for key in SIGNALS}
    recommendations = []

    def add(identifier, severity, title, action, verification, **evidence):
        recommendations.append({
            "id": identifier,
            "severity": severity,
            "title": title,
            "evidence": {key: round(value, 1) for key, value in evidence.items() if value is not None},
            "action": action,
            "verification": verification,
        })

    if all(value is None for value in values.values()):
        add(
            "signals-unavailable", "warning", "Aucune mesure disponible",
            "Vérifier les sources du rapport et l’état des services dans la Vue d’ensemble.",
            "Relancer le rapport après le retour d’au moins une collecte.",
            mesures_disponibles=0,
        )
        return recommendations

    targets_down = values["targets_down"]
    windows_age = values["host_sample_age_seconds"]
    if targets_down is not None and targets_down >= 1:
        add(
            "observability-targets-down", "critical" if targets_down >= 3 else "warning",
            "Cibles de collecte indisponibles",
            "Identifier les cibles à zéro dans la Vue d’ensemble et vérifier la disponibilité des services correspondants.",
            "Vérifier que toutes les cibles attendues repassent à up=1 et que leurs métriques se renouvellent.",
            cibles_indisponibles=targets_down,
        )
    if windows_age is not None and windows_age >= 45:
        add(
            "windows-samples-stale", "warning", "Mesures Windows anciennes",
            "Contrôler le processus du collecteur Windows et son journal local. Redémarrer le collecteur si sa boucle est arrêtée.",
            "Vérifier que l’âge du relevé retombe sous 45 secondes et reste bas sur plusieurs scrapes.",
            age_releve_secondes=windows_age,
        )
    elif windows_age is None and targets_down == 0:
        add(
            "windows-samples-missing", "info", "Relevé Windows absent",
            "Si la collecte Windows est attendue, vérifier la présence du fichier de métriques et l’état du collecteur Windows.",
            "Retrouver un âge de relevé mesuré et un inventaire de processus dans le tableau Windows.",
            cibles_indisponibles=targets_down,
        )

    rejections = values["collector_rejected"]
    if rejections is not None and rejections >= 1:
        add(
            "otel-rejections", "warning", "Données OTLP refusées",
            "Examiner les compteurs de refus et les journaux du Collector. Vérifier la saturation mémoire et les erreurs de pipeline.",
            "Relancer le test synthétique et vérifier que le compteur ne croît plus sous une charge comparable.",
            rejets_estimes=rejections,
        )

    http_attempts = values["http_attempts"]
    failures = values["http_failures"]
    failure_share = _share(failures, http_attempts)
    if http_attempts is not None and http_attempts >= 20 and failures is not None and failures >= 3 and failure_share is not None and failure_share >= 0.10:
        add(
            "http-failures", "warning", "Échecs HTTP fréquents",
            "Ventiler les échecs par statut dans Requêtes pour distinguer les réponses 429, 5xx et les tentatives sans statut.",
            "Comparer le taux d’échec après correction sur une fenêtre avec un volume de tentatives similaire.",
            tentatives_http=http_attempts, echecs_http=failures, part_echecs_pct=failure_share * 100,
        )

    retries = values["http_retries"]
    if retries is not None and retries >= 3:
        add(
            "http-retries", "info", "Retentatives HTTP observées",
            "Examiner dans Loki les événements de tentative dont l’index est supérieur à zéro, puis les rapprocher des statuts disponibles.",
            "Comparer le nombre de retentatives sur des exécutions de volume comparable après avoir corrigé la cause confirmée.",
            tentatives_http=http_attempts, retentatives_http=retries,
        )

    ws_sends = values["ws_sends"]
    ws_full = values["ws_full_resends"]
    ws_share = _share(ws_full, ws_sends)
    if ws_sends is not None and ws_sends >= 20 and ws_full is not None and ws_full >= 5 and ws_share is not None and ws_share >= 0.25:
        add(
            "ws-full-context", "info", "Renvois de contexte complet à examiner",
            "Examiner les motifs du mode full dans Requêtes. Rapprocher les changements de contexte et les pertes de connexion des renvois observés.",
            "Comparer la part des renvois à contexte complet sur des tours comparables. Ce signal mesure le transport.",
            envois_generation_ws=ws_sends, renvois_complets_ws=ws_full, part_renvois_pct=ws_share * 100,
        )

    completions = values["completion_events"]
    input_tokens = values["input_tokens"]
    cached_tokens = values["cached_input_tokens"]
    cache_share = _share(cached_tokens, input_tokens)
    if completions is not None and completions >= 10:
        if input_tokens is None or cached_tokens is None or cached_tokens > input_tokens:
            add(
                "token-coverage", "info", "Couverture des tokens incomplète ou incohérente",
                "Vérifier que les spans de fin de réponse publient les attributs numériques d’entrée et de cache dans Tempo.",
                "Confirmer la présence des deux valeurs sur les mêmes spans, avec un cache inférieur ou égal à l’entrée.",
                fins_reponse_observees=completions, tokens_entree=input_tokens, tokens_cache=cached_tokens,
            )
        elif completions >= 20 and input_tokens >= 10_000 and cache_share is not None and cache_share < 0.10:
            add(
                "cache-read-share-low", "info", "Faible réutilisation du cache d’entrée",
                "Comparer des tours comparables avec le même modèle et un préfixe stable. Des entrées nouvelles peuvent expliquer cette part.",
                "Observer l’évolution de la part des tokens lus en cache sur des tours comparables et juger en parallèle la latence et le résultat.",
                fins_reponse_observees=completions, tokens_entree=input_tokens, tokens_cache=cached_tokens,
                part_cache_pct=cache_share * 100,
            )

    tool_calls = values["tool_calls"]
    failed_tools = values["failed_tool_calls"]
    tool_share = _share(failed_tools, tool_calls)
    if tool_calls is not None and tool_calls >= 10 and failed_tools is not None and failed_tools >= 3 and tool_share is not None and tool_share >= 0.15:
        add(
            "tool-failures", "warning", "Échecs d’outils fréquents",
            "Chercher les outils concernés dans Usage. Distinguer les problèmes de disponibilité, les entrées invalides et les décisions de permission.",
            "Vérifier une baisse des échecs après correction sur des appels comparables et confirmer les résultats des tâches.",
            appels_outils=tool_calls, echecs_outils=failed_tools, part_echecs_pct=tool_share * 100,
        )

    turn_count = values["turn_count"]
    turn_p95 = values["turn_p95_seconds"]
    if turn_count is not None and turn_count >= 20 and turn_p95 is not None and turn_p95 >= 60:
        add(
            "turn-p95-high", "info", "Latence élevée sur les tours les plus longs",
            "Examiner le chemin critique disponible et les traces des tours longs. Vérifier la contribution de l’inférence, des outils et des approbations.",
            "Comparer le p95 et l’effectif après correction sur des tâches et des modèles similaires.",
            tours_observes=turn_count, p95_tour_secondes=turn_p95,
        )

    return sorted(recommendations, key=lambda item: SEVERITY_ORDER[item["severity"]])


def _self_test() -> None:
    import unittest

    class EngineTests(unittest.TestCase):
        def test_no_data_and_invalid_measurements(self):
            for snapshot in ({}, {"targets_down": True, "turn_count": float("inf"), "http_attempts": "50", "input_tokens": 10**500}):
                self.assertEqual([result["id"] for result in recommend(snapshot)], ["signals-unavailable"])

        def test_healthy_snapshot_reports_no_issue(self):
            self.assertEqual(recommend({"targets_down": 0, "host_sample_age_seconds": 3, "collector_rejected": 0}), [])

        def test_health_and_priorities(self):
            results = recommend({"targets_down": 3, "host_sample_age_seconds": 45, "collector_rejected": 2, "http_retries": 3})
            self.assertEqual([item["id"] for item in results], ["observability-targets-down", "windows-samples-stale", "otel-rejections", "http-retries"])
            self.assertEqual([item["severity"] for item in results], ["critical", "warning", "warning", "info"])
            self.assertEqual(recommend({"targets_down": 0})[0]["id"], "windows-samples-missing")

        def test_rules_at_thresholds(self):
            results = recommend({
                "http_attempts": 30, "http_failures": 3, "http_retries": 3,
                "ws_sends": 20, "ws_full_resends": 5, "completion_events": 20,
                "input_tokens": 10_000, "cached_input_tokens": 500,
                "tool_calls": 20, "failed_tool_calls": 3, "turn_count": 20, "turn_p95_seconds": 60,
            })
            self.assertEqual({item["id"] for item in results}, {
                "http-failures", "http-retries", "ws-full-context", "cache-read-share-low", "tool-failures", "turn-p95-high",
            })
            for item in results:
                self.assertTrue(all(isinstance(value, (int, float)) for value in item["evidence"].values()))

        def test_missing_or_inconsistent_data_is_never_a_zero(self):
            first = recommend({"completion_events": 20, "input_tokens": 10_000, "cached_input_tokens": None, "turn_count": 19, "turn_p95_seconds": 100, "http_attempts": 20, "http_failures": 21})
            second = recommend({"completion_events": 20, "input_tokens": 10_000, "cached_input_tokens": 10_001})
            self.assertEqual([item["id"] for item in first], ["token-coverage"])
            self.assertEqual([item["id"] for item in second], ["token-coverage"])
            self.assertNotIn("tokens_cache", first[0]["evidence"])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EngineTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate local advisor rules with synthetic measurements.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test:
        parser.error("use --self-test or import recommend from the advisor application")
    _self_test()
