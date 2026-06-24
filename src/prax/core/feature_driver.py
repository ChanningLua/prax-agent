"""Feature driver — advance a feature_list.json one item at a time.

Sits ABOVE the pure OrchestratorLoop (never inside it): repeatedly pick the
highest-priority unfinished feature, run it to a verdict, and mark it done ONLY
when verified — then move to the next. Stops cleanly when the list is exhausted,
a feature can't be verified (don't thrash through the rest), or a per-window
feature cap is hit. State lives in feature_list.json (done items persist), so a
crashed/quota-stopped window resumes at the right feature.

The actual per-feature execution is injected as ``run_feature(feature) ->
OrchestrationOutcome`` so this module is testable without a real claude -p, and
the loop stays a pure goal→outcome machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .feature_list import Feature, FeatureList
from .orchestrator_loop import OrchestrationOutcome


@dataclass
class FeatureResult:
    feature_id: str
    title: str
    outcome: OrchestrationOutcome


@dataclass
class FeatureRunReport:
    # all_features_done | feature_blocked | max_features | no_features
    stop_reason: str
    completed: list[str] = field(default_factory=list)   # ids marked done THIS run
    results: list[FeatureResult] = field(default_factory=list)
    blocked_id: str | None = None

    @property
    def all_done(self) -> bool:
        return self.stop_reason == "all_features_done"


RunFeature = Callable[[Feature], OrchestrationOutcome]


def run_features(
    feature_list: FeatureList,
    run_feature: RunFeature,
    *,
    max_features: int | None = None,
    on_feature: Callable[[Feature, OrchestrationOutcome], None] | None = None,
) -> FeatureRunReport:
    """Drive ``feature_list`` to completion (or to the first blocker).

    Re-reads ``next_pending()`` each round so persisted ``done`` status (incl.
    from a prior crashed window) is always respected.
    """
    completed: list[str] = []
    results: list[FeatureResult] = []
    started = 0

    while True:
        feature = feature_list.next_pending()
        if feature is None:
            # Nothing left to do → done, REGARDLESS of the per-window cap. (Check
            # this before the cap so finishing the last feature exactly as the cap
            # is hit reports all_features_done, not a misleading max_features.)
            return FeatureRunReport("all_features_done", completed, results)
        if max_features is not None and started >= max_features:
            # cap hit AND there is still pending work being deferred to next window
            return FeatureRunReport("max_features", completed, results)

        started += 1
        outcome = run_feature(feature)
        results.append(FeatureResult(feature.id, feature.title, outcome))
        if on_feature is not None:
            on_feature(feature, outcome)

        if outcome.verified:
            feature_list.mark_done(feature.id)
            completed.append(feature.id)
            continue

        # Not verified → stop at this feature rather than thrashing the rest
        # (highest-priority unfinished work is blocked; a human should look).
        return FeatureRunReport("feature_blocked", completed, results, blocked_id=feature.id)
