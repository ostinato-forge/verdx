"""Flywheel API — log corrections, log patterns, search, annotate, review."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from verdx.engine.flywheel.ids import generate_id
from verdx.engine.flywheel.matching import (
    MERGE_THRESHOLD,
    find_best_matches,
    jaccard,
    tokenize,
)
from verdx.engine.flywheel.store import FlywheelStore
from verdx.engine.flywheel.types import Annotation, Correction, Pattern

MAX_REPO_PATTERNS = 200
MAX_ORG_PATTERNS = 500
MAX_CORRECTIONS = 1000
STALE_DAYS = 90

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _parse_iso(iso_str: str | None) -> datetime:
    if not iso_str:
        return datetime(2020, 1, 1, tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return datetime(2020, 1, 1, tzinfo=UTC)


def _relevance_score(pattern: Pattern, now: datetime) -> float:
    updated = _parse_iso(pattern.updated)
    age_days = max(1.0, (now - updated).total_seconds() / 86400)
    recency = 1.0 / (1.0 + age_days / STALE_DAYS)
    target_breadth = min(len(pattern.targets), 10)
    return pattern.seen_count * recency * (1.0 + 0.1 * target_breadth)


def _compact_patterns(
    patterns: list[Pattern],
    *,
    max_patterns: int,
    stale_days: int = STALE_DAYS,
) -> tuple[list[Pattern], int]:
    """Keep highest-relevance patterns up to max_patterns."""
    if len(patterns) <= max_patterns:
        return patterns, 0
    now = datetime.now(tz=UTC)
    scored = sorted(
        patterns,
        key=lambda p: _relevance_score(p, now),
        reverse=True,
    )
    return scored[:max_patterns], len(patterns) - max_patterns


def _compact_corrections(
    corrections: list[Correction],
    *,
    max_corrections: int,
) -> tuple[list[Correction], int]:
    """Deduplicate by (sig, target) keeping latest, then cap by recency."""
    latest: dict[tuple[str, str], Correction] = {}
    for c in corrections:
        key = (c.observation_signature, c.target_key)
        existing = latest.get(key)
        if existing is None or c.timestamp > existing.timestamp:
            latest[key] = c
    deduped = sorted(latest.values(), key=lambda c: c.timestamp, reverse=True)
    if len(deduped) > max_corrections:
        deduped = deduped[:max_corrections]
    return deduped, len(corrections) - len(deduped)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _confidence_label(seen_count: int) -> str:
    if seen_count >= 3:
        return "high"
    if seen_count >= 2:
        return "medium"
    return "low"


def _merge_into_org(
    candidates: Sequence[Pattern],
    existing: list[Pattern],
) -> tuple[list[Pattern], int]:
    """Merge candidate patterns into existing org patterns.

    Uses Jaccard similarity to decide merge vs. create. Returns
    (full_org_list, count_of_newly_created_patterns).
    """
    org = list(existing)
    new_count = 0

    for candidate in candidates:
        candidate_tokens = frozenset(candidate.symptoms)
        best_match: Pattern | None = None
        best_score = 0.0

        for org_pat in org:
            score = jaccard(candidate_tokens, frozenset(org_pat.symptoms))
            if score > best_score:
                best_score = score
                best_match = org_pat

        now = _now_iso()

        if best_match is not None and best_score >= MERGE_THRESHOLD:
            best_match.symptoms = sorted(set(best_match.symptoms) | set(candidate.symptoms))
            best_match.seen_count += candidate.seen_count
            best_match.updated = now
            for t in candidate.targets:
                if t not in best_match.targets:
                    best_match.targets.append(t)
            for s in candidate.observation_signatures:
                if s not in best_match.observation_signatures:
                    best_match.observation_signatures.append(s)
        else:
            new_pat = Pattern(
                id=generate_id("org"),
                symptoms=sorted(candidate.symptoms),
                diagnosis=candidate.diagnosis,
                resolution=candidate.resolution,
                category=candidate.category,
                domain=candidate.domain,
                seen_count=candidate.seen_count,
                observation_signatures=list(candidate.observation_signatures),
                targets=list(candidate.targets),
                created=candidate.created or now,
                updated=now,
            )
            org.append(new_pat)
            new_count += 1

    return org, new_count


def categorize_resolution(resolution: str) -> str | None:
    """Categorize a pattern's resolution into a bucket for contradiction detection.

    Returns one of: "fix", "accept", "platform", or None (uncategorized).
    """
    lower = resolution.lower()
    fix_keywords = (
        "fix",
        "update",
        "replace",
        "remove",
        "add",
        "correct",
        "rename",
        "rewrite",
    )
    accept_keywords = (
        "accepted",
        "by design",
        "intentional",
        "won't fix",
        "wontfix",
        "false positive",
        "expected",
        "standard practice",
        "lab convention",
    )
    platform_keywords = (
        "platform",
        "rendering",
        "flamel",
        "rol",
        "display",
        "scaffold",
        "build pipeline",
        "epub",
        "video",
    )
    if any(k in lower for k in accept_keywords):
        return "accept"
    if any(k in lower for k in platform_keywords):
        return "platform"
    if any(k in lower for k in fix_keywords):
        return "fix"
    return None


CONTRADICTORY_PAIRS = frozenset({("accept", "fix"), ("fix", "platform")})


def detect_contradictions(patterns: list[Pattern]) -> list[dict[str, Any]]:
    """Find pattern pairs with similar symptoms but opposite resolutions."""
    results: list[dict[str, Any]] = []
    for i, p1 in enumerate(patterns):
        cat1 = categorize_resolution(p1.resolution)
        if cat1 is None:
            continue
        tokens1 = frozenset(p1.symptoms)
        for p2 in patterns[i + 1 :]:
            cat2 = categorize_resolution(p2.resolution)
            if cat2 is None:
                continue
            pair = (min(cat1, cat2), max(cat1, cat2))
            if pair not in CONTRADICTORY_PAIRS:
                continue
            score = jaccard(tokens1, frozenset(p2.symptoms))
            if score >= MERGE_THRESHOLD:
                results.append(
                    {
                        "type": "contradiction",
                        "pattern_ids": [p1.id, p2.id],
                        "similarity": round(score, 3),
                        "detail": f"{p1.id} ({cat1}) vs {p2.id} ({cat2})",
                    }
                )
    return results


class Flywheel:
    """Learning engine for verdx validation and fixing."""

    def __init__(self, store: FlywheelStore) -> None:
        self._store = store

    def block(
        self,
        pattern_id: str,
        *,
        reason: str,
        blocked_by: str = "",
    ) -> dict[str, Any]:
        """Add a pattern to the blocklist."""
        entries = self._store.load_blocked()
        if any(e["pattern_id"] == pattern_id for e in entries):
            return {"action": "already_blocked", "pattern_id": pattern_id}
        entries.append(
            {
                "pattern_id": pattern_id,
                "reason": reason,
                "blocked_by": blocked_by,
                "blocked_at": _now_iso(),
            }
        )
        path = self._store.save_blocked(entries)
        return {"action": "blocked", "pattern_id": pattern_id, "file": str(path)}

    def unblock(self, pattern_id: str) -> dict[str, Any]:
        """Remove a pattern from the blocklist."""
        entries = self._store.load_blocked()
        before = len(entries)
        entries = [e for e in entries if e["pattern_id"] != pattern_id]
        if len(entries) == before:
            return {"action": "not_found", "pattern_id": pattern_id}
        self._store.save_blocked(entries)
        return {"action": "unblocked", "pattern_id": pattern_id}

    def log_correction(
        self,
        *,
        observation_signature: str,
        target_key: str,
        initial_severity: str,
        final_decision: str,
        description: str = "",
        domain: str = "unknown",
    ) -> dict[str, Any]:
        """Record a developer decision — both overrides and confirmations."""
        correction = Correction(
            observation_signature=observation_signature,
            target_key=target_key,
            initial_severity=initial_severity,
            final_decision=final_decision,
            description=description,
            domain=domain,
            timestamp=_now_iso(),
        )
        path = self._store.append_correction(correction)
        total = len(self._store.load_corrections())
        is_override = initial_severity != final_decision
        return {
            "logged": True,
            "file": str(path),
            "total_corrections": total,
            "confirmed": not is_override,
        }

    def log_pattern(
        self,
        *,
        target_key: str,
        symptoms: str,
        diagnosis: str,
        resolution: str,
        category: str = "",
        domain: str = "unknown",
        observation_signatures: list[str] | None = None,
        source: str = "",
    ) -> dict[str, Any]:
        """Record a learned pattern. Merges if Jaccard >= MERGE_THRESHOLD."""
        query_tokens = tokenize(symptoms)
        if not query_tokens:
            return {"action": "skipped", "reason": "no_tokens"}

        patterns = self._store.load_patterns()
        best_match: Pattern | None = None
        best_score = 0.0

        for pattern in patterns:
            score = jaccard(query_tokens, frozenset(pattern.symptoms))
            if score > best_score:
                best_score = score
                best_match = pattern

        now = _now_iso()

        if best_match is not None and best_score >= MERGE_THRESHOLD:
            merged_symptoms = sorted(set(best_match.symptoms) | set(query_tokens))
            best_match.symptoms = merged_symptoms
            best_match.seen_count += 1
            best_match.updated = now
            if target_key not in best_match.targets:
                best_match.targets.append(target_key)
            if observation_signatures:
                for sig in observation_signatures:
                    if sig not in best_match.observation_signatures:
                        best_match.observation_signatures.append(sig)
            self._store.save_patterns(patterns)
            return {
                "action": "updated",
                "pattern_id": best_match.id,
                "seen_count": best_match.seen_count,
            }

        new_pattern = Pattern(
            id=generate_id("pat"),
            symptoms=sorted(query_tokens),
            diagnosis=diagnosis,
            resolution=resolution,
            category=category,
            domain=domain,
            seen_count=1,
            observation_signatures=observation_signatures or [],
            targets=[target_key],
            source=source,
            created=now,
            updated=now,
        )
        patterns.append(new_pattern)
        self._store.save_patterns(patterns)
        return {
            "action": "created",
            "pattern_id": new_pattern.id,
            "seen_count": 1,
        }

    def search(
        self,
        keywords: str,
        *,
        domain: str | None = None,
        top: int = 3,
    ) -> list[dict[str, Any]]:
        """Search patterns by keyword similarity."""
        query_tokens = tokenize(keywords)
        if not query_tokens:
            return []

        blocked = self._store.blocked_ids()
        patterns = [p for p in self._store.load_patterns() if p.id not in blocked]
        matches = find_best_matches(query_tokens, patterns, domain=domain, top=top)

        return [
            {
                "pattern_id": pattern.id,
                "score": round(score, 3),
                "symptoms": pattern.symptoms,
                "diagnosis": pattern.diagnosis,
                "resolution": pattern.resolution,
                "category": pattern.category,
                "seen_count": pattern.seen_count,
                "targets": pattern.targets,
            }
            for pattern, score in matches
        ]

    def annotate(
        self,
        observations: Sequence[object],
        *,
        domain: str | None = None,
    ) -> list[Annotation]:
        """Map each observation to its best-matching pattern."""
        blocked = self._store.blocked_ids()
        patterns = [p for p in self._store.load_patterns() if p.id not in blocked]
        corrections = self._store.load_corrections()

        if not patterns and not corrections:
            return []

        correction_sigs = {c.observation_signature for c in corrections}

        annotations: list[Annotation] = []
        for obs in observations:
            sig = getattr(obs, "signature", None)
            msg = getattr(obs, "message", "")
            if not sig or not msg:
                continue

            query_tokens = tokenize(msg)
            matches = (
                find_best_matches(query_tokens, patterns, domain=domain, top=1) if patterns else []
            )

            if matches:
                pattern, score = matches[0]
                suggested = None
                if pattern.seen_count >= 2 and score >= 0.4:
                    fp_count = sum(
                        1
                        for c in corrections
                        if c.final_decision == "false-positive"
                        and c.observation_signature in set(pattern.observation_signatures)
                    )
                    if fp_count >= 2:
                        suggested = "false-positive"
                    elif any(
                        c.final_decision == "accepted-risk"
                        for c in corrections
                        if c.observation_signature in set(pattern.observation_signatures)
                    ):
                        suggested = "accepted-risk"

                confidence = _confidence_label(pattern.seen_count)
                target_list = ", ".join(pattern.targets[:5])
                rationale = (
                    f"Seen {pattern.seen_count} time(s) "
                    f"in {target_list}. "
                    f"Diagnosis: {pattern.diagnosis}"
                )

                annotations.append(
                    Annotation(
                        observation_signature=sig,
                        pattern_id=pattern.id,
                        match_score=round(score, 3),
                        suggested_decision=suggested,
                        confidence=confidence,
                        rationale=rationale,
                    )
                )
            elif sig in correction_sigs:
                matching_corrections = [c for c in corrections if c.observation_signature == sig]
                latest = matching_corrections[-1]
                annotations.append(
                    Annotation(
                        observation_signature=sig,
                        pattern_id=None,
                        match_score=0.0,
                        suggested_decision=latest.final_decision,
                        confidence="low",
                        rationale=(
                            f"Previously overridden as "
                            f"{latest.final_decision}: "
                            f"{latest.description}"
                        ),
                    )
                )

        return annotations

    def review(self) -> dict[str, Any]:
        """Summary of corrections, patterns, and potential issues."""
        corrections = self._store.load_corrections()
        blocked = self._store.blocked_ids()
        patterns = self._store.load_patterns()

        transitions: Counter[str] = Counter()
        by_domain: Counter[str] = Counter()
        by_target: Counter[str] = Counter()
        for c in corrections:
            transitions[f"{c.initial_severity}→{c.final_decision}"] += 1
            by_domain[c.domain] += 1
            by_target[c.target_key] += 1

        pattern_summary = [
            {
                "id": p.id,
                "seen_count": p.seen_count,
                "diagnosis": p.diagnosis,
                "targets": p.targets,
                "category": p.category,
                "blocked": p.id in blocked,
                "source": p.source,
            }
            for p in patterns
        ]
        pattern_summary.sort(key=lambda x: -x["seen_count"])  # type: ignore[operator]

        active = [p for p in patterns if p.id not in blocked]
        issues = self.detect_issues(active, corrections)

        return {
            "corrections_summary": {
                "total": len(corrections),
                "transitions": dict(transitions),
                "by_domain": dict(by_domain),
                "by_target": dict(by_target),
            },
            "patterns_summary": {
                "total": len(patterns),
                "blocked": len(blocked),
                "by_frequency": pattern_summary,
            },
            "issues": issues,
        }

    def detect_issues(
        self,
        patterns: list[Pattern],
        corrections: list[Correction],
    ) -> list[dict[str, Any]]:
        """Detect contradictions, staleness, and other flywheel health issues."""
        issues: list[dict[str, Any]] = []

        now = datetime.now(tz=UTC)
        for p in patterns:
            updated = _parse_iso(p.updated)
            age_days = (now - updated).total_seconds() / 86400
            if age_days > STALE_DAYS:
                issues.append(
                    {
                        "type": "stale",
                        "pattern_id": p.id,
                        "detail": (
                            f"Last updated {int(age_days)} days ago (threshold: {STALE_DAYS})"
                        ),
                    }
                )
            if p.seen_count == 1 and len(p.targets) <= 1:
                issues.append(
                    {
                        "type": "low-evidence",
                        "pattern_id": p.id,
                        "detail": "Seen only 1x — needs more data before promotion",
                    }
                )

        correction_overrides: Counter[str] = Counter()
        for c in corrections:
            if c.initial_severity != c.final_decision:
                for p in patterns:
                    if c.observation_signature in p.observation_signatures:
                        correction_overrides[p.id] += 1
        for pid, count in correction_overrides.items():
            if count >= 3:
                issues.append(
                    {
                        "type": "frequently-overridden",
                        "pattern_id": pid,
                        "detail": f"{count} corrections override this pattern — consider blocking",
                    }
                )

        contradictions = detect_contradictions(patterns)
        issues.extend(contradictions)

        return issues

    def promote(
        self,
        *,
        min_repos: int = 3,
    ) -> dict[str, Any]:
        """Promote cross-repo patterns to org layer.

        Scans all repos in the data directory, groups patterns by Jaccard
        similarity, and promotes groups spanning min_repos repos.
        """
        repo_keys = self._store.list_repo_keys()
        if not repo_keys:
            return {"promoted": 0, "repos_scanned": 0, "patterns": []}

        blocked = self._store.blocked_ids()

        repo_patterns: list[tuple[str, Pattern]] = []
        repos_scanned = 0

        for key in repo_keys:
            store = FlywheelStore(key, data_root=self._store._root)
            patterns = store.load_repo_patterns()
            repos_scanned += 1
            for p in patterns:
                if p.id not in blocked:
                    repo_patterns.append((key, p))

        if not repo_patterns:
            return {"promoted": 0, "repos_scanned": repos_scanned, "patterns": []}

        groups: list[list[tuple[str, Pattern]]] = []
        assigned: set[int] = set()

        for i, (_repo_i, pat_i) in enumerate(repo_patterns):
            if i in assigned:
                continue
            group = [repo_patterns[i]]
            assigned.add(i)
            tokens_i = frozenset(pat_i.symptoms)

            for j in range(i + 1, len(repo_patterns)):
                if j in assigned:
                    continue
                _, pat_j = repo_patterns[j]
                if jaccard(tokens_i, frozenset(pat_j.symptoms)) >= MERGE_THRESHOLD:
                    group.append(repo_patterns[j])
                    assigned.add(j)

            groups.append(group)

        candidates: list[Pattern] = []
        for group in groups:
            distinct_repos = {rn for rn, _ in group}
            if len(distinct_repos) < min_repos:
                continue

            _, base = group[0]
            merged_symptoms: set[str] = set(base.symptoms)
            merged_targets: list[str] = list(base.targets)
            merged_sigs: list[str] = list(base.observation_signatures)
            total_seen = base.seen_count

            for _rn, pat in group[1:]:
                merged_symptoms.update(pat.symptoms)
                total_seen += pat.seen_count
                for t in pat.targets:
                    if t not in merged_targets:
                        merged_targets.append(t)
                for s in pat.observation_signatures:
                    if s not in merged_sigs:
                        merged_sigs.append(s)

            for rn, _ in group:
                if rn not in merged_targets:
                    merged_targets.append(rn)

            candidates.append(
                Pattern(
                    id="",
                    symptoms=sorted(merged_symptoms),
                    diagnosis=base.diagnosis,
                    resolution=base.resolution,
                    category=base.category,
                    domain=base.domain,
                    seen_count=total_seen,
                    observation_signatures=merged_sigs,
                    targets=merged_targets,
                    created=base.created,
                    updated="",
                )
            )

        if not candidates:
            return {"promoted": 0, "repos_scanned": repos_scanned, "patterns": []}

        existing = self._store.load_org_patterns()
        merged_org, new_count = _merge_into_org(candidates, existing)

        org_pruned = 0
        if len(merged_org) > MAX_ORG_PATTERNS:
            merged_org, org_pruned = _compact_patterns(merged_org, max_patterns=MAX_ORG_PATTERNS)

        for pat in merged_org:
            self._store.save_org_pattern(pat)

        all_corrections: list[Correction] = []
        for key in repo_keys:
            store = FlywheelStore(key, data_root=self._store._root)
            all_corrections.extend(store.load_corrections())

        for pat in merged_org:
            if not pat.id.startswith("org"):
                continue
            matching = [
                c
                for c in all_corrections
                if c.observation_signature in set(pat.observation_signatures)
                and c.initial_severity != c.final_decision
            ]
            repos_with_corrections = {c.target_key for c in matching}
            if len(matching) >= 10 and len(repos_with_corrections) >= 5:
                _log.info(
                    "Pattern %s qualifies for org override — "
                    "run `verdx flywheel override %s` to approve",
                    pat.id,
                    pat.id,
                )

        return {
            "promoted": new_count,
            "repos_scanned": repos_scanned,
            "total_org_patterns": len(merged_org),
            "org_pruned": org_pruned,
            "patterns": [
                {"id": p.id, "targets": p.targets, "seen_count": p.seen_count} for p in merged_org
            ],
        }

    def compact(
        self,
        *,
        max_patterns: int = MAX_REPO_PATTERNS,
        max_corrections: int = MAX_CORRECTIONS,
    ) -> dict[str, Any]:
        """Compact repo-layer flywheel data when over capacity."""
        result: dict[str, Any] = {
            "patterns_pruned": 0,
            "corrections_pruned": 0,
        }

        patterns = self._store.load_repo_patterns()
        if len(patterns) > max_patterns:
            compacted, pruned = _compact_patterns(patterns, max_patterns=max_patterns)
            self._store.save_patterns(compacted)
            result["patterns_pruned"] = pruned

        corrections = self._store.load_corrections()
        if len(corrections) > max_corrections:
            compacted_c, pruned_c = _compact_corrections(
                corrections, max_corrections=max_corrections
            )
            self._store.save_corrections(compacted_c)
            result["corrections_pruned"] = pruned_c

        return result
