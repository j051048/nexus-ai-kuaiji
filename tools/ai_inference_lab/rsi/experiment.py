"""Bounded offline experiments on H01 execution and AG05 scoring.

The trusted preparer freezes files without interpreting holdout gold. Only after
an immutable selection receipt exists may the evaluator open validation data.
This is API separation, not a sandbox against arbitrary code with filesystem access.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import time

from tools.ocr_replay import replay
from tools.ai_inference_lab.examples import (
    CandidateConfig,
    EvaluationSpec,
    UnknownAttempt,
    evaluate_samples,
    evaluation_report,
)
from .snapshots import Store, SnapshotError, atomic_create, canonical, dependencies, digest, read

ROOT = Path(__file__).resolve().parents[3]


def validate_usage(value):
    if value is None:
        return None
    if (
        type(value) is not dict
        or set(value) != {"input", "output"}
        or any(type(v) is not int or not 0 <= v <= 10_000_000 for v in value.values())
        or sum(value.values()) > 10_000_000
    ):
        raise SnapshotError("usage_invalid")
    return value


def validate_cost(value):
    if value is None:
        return None
    if type(value) is not str:
        raise SnapshotError("cost_invalid")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise SnapshotError("cost_invalid") from None
    if not number.is_finite() or number < 0:
        raise SnapshotError("cost_invalid")
    return value


def freeze(
    store: Store,
    manifest_path: Path,
    baseline,
    candidates,
    out: Path,
    *,
    max_calls=100,
    max_seconds=60,
    archives: Path | None = None,
    recorded_model="none",
):
    if (
        type(candidates) is not list
        or not 1 <= len(candidates) <= 3
        or len(set(candidates)) != len(candidates)
        or baseline in candidates
    ):
        raise SnapshotError("candidate_limit")
    if (
        type(max_calls) is not int
        or not 0 <= max_calls <= 2000
        or type(max_seconds) is not int
        or not 0 <= max_seconds <= 3600
    ):
        raise SnapshotError("budget_invalid")
    if (
        type(recorded_model) is not str
        or not 1 <= len(recorded_model) <= 200
        or (archives is None and recorded_model != "none")
    ):
        raise SnapshotError("recorded_model_invalid")
    snapshots = [store.load(cid) for cid in [baseline, *candidates]]
    if any(s["root_id"] != snapshots[0]["root_id"] for s in snapshots):
        raise SnapshotError("unrelated_lineage")
    raw = read(manifest_path)
    rows = raw.get("samples", [])
    if not rows or len(rows) > 500 or len({s["sample_id"] for s in rows}) != len(rows):
        raise SnapshotError("sample_set_invalid")
    seen = {}
    holdout_sources = set()
    for sample in rows:
        sources = {
            sample["source_hash"],
            sample["ocr_sha256"].lower(),
            *sample.get("ancestor_source_hashes", []),
        }
        if sample["split"] == "validation":
            holdout_sources |= sources
        for source in sources:
            if source in seen and seen[source] != sample["sample_id"]:
                raise SnapshotError("source_leak_or_duplicate")
            seen[source] = sample["sample_id"]
    if any(
        (store.root / "validation-use" / (digest(source) + ".json")).exists()
        for source in holdout_sources
    ):
        raise SnapshotError("holdout_exposed_requires_new_dataset")
    if {s["split"] for s in rows} != {"dev", "validation"}:
        raise SnapshotError("both_splits_required")
    if out.exists():
        raise SnapshotError("run_already_exists")
    out.mkdir(parents=True)
    manifest_hashes, assets = {}, {}
    # Copy immutable inputs with exact hashes. No gold parsing in preparation.
    for split in ("screen", "dev", "validation"):
        selected = [s for s in rows if s["split"] == ("dev" if split == "screen" else split)]
        if split == "screen":
            selected = selected[:2]
        for sample in selected:
            for field in ("ocr", "gold"):
                source = replay._safe(manifest_path.parent, sample[field + "_path"])
                if source.stat().st_size > replay.MAX_FILE:
                    raise SnapshotError("input_too_large")
                content = source.read_bytes()
                if replay.sha256_bytes(content) != sample[field + "_sha256"].lower():
                    raise SnapshotError("input_hash")
                relative = Path(split) / sample[field + "_path"]
                target = out / relative
                if out.resolve() not in target.resolve().parents:
                    raise SnapshotError("input_path")
                atomic_create(target, content)
                assets[relative.as_posix()] = replay.sha256_bytes(content)
        manifest = {**raw, "samples": selected}
        path = out / split / "manifest.json"
        atomic_create(path, canonical(manifest))
        manifest_hashes[split] = replay.sha256_path(path)
    plan = {
        "schema_version": "rsi-experiment-v1",
        "baseline": baseline,
        "candidates": candidates,
        "snapshots": {s["id"]: s["content_sha256"] for s in snapshots},
        "mode": "archived_results" if archives is not None else "mock",
        "model": recorded_model,
        "original_manifest_sha256": replay.sha256_path(manifest_path),
        "holdout_sources": sorted(holdout_sources),
        "manifests": manifest_hashes,
        "assets": assets,
        "dependencies": dependencies(ROOT),
        "max_calls": max_calls,
        "max_seconds": max_seconds,
        "selection_policy": "ag05-veto-improvement-baseline-tie-v1",
    }
    plan["archives"] = {}
    if archives is not None:
        for split in manifest_hashes:
            for cid in [baseline, *candidates]:
                relative = f"archives/{split}-{cid}.json"
                source = replay._safe(archives, f"{split}-{cid}.json")
                if source.stat().st_size > replay.MAX_FILE:
                    raise SnapshotError("archive_too_large")
                content = source.read_bytes()
                if len(content) > replay.MAX_FILE:
                    raise SnapshotError("archive_too_large")
                atomic_create(out / relative, content)
                plan["archives"][relative] = replay.sha256_bytes(content)
    atomic_create(out / "plan.json", canonical({"plan": plan, "sha256": digest(plan)}))
    atomic_create(out / "plan.sha256", digest(plan).encode("ascii"))
    return plan


@contextmanager
def exclusive_run(root):
    """OS-released advisory file lock; interrupted attempts remain unknown."""
    with (root / ".lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class Experiment:
    def __init__(self, store: Store, root: Path, *, clock=time.monotonic):
        self.store, self.root, self.clock = store, Path(root), clock

    def _config(self, cid):
        policy = self.store.policy(cid)
        return CandidateConfig(
            cid, policy.version, policy.prompt, "rsi-context-v1", mode=self.plan["mode"]
        )

    def _validate(self):
        envelope = read(self.root / "plan.json")
        plan = envelope["plan"]
        if (
            digest(plan) != envelope["sha256"]
            or digest(plan) != (self.root / "plan.sha256").read_text(encoding="ascii")
            or plan["schema_version"] != "rsi-experiment-v1"
        ):
            raise SnapshotError("plan_digest")
        if plan["dependencies"] != dependencies(ROOT):
            raise SnapshotError("dependencies_changed")
        for cid, content_hash in plan["snapshots"].items():
            if self.store.load(cid)["content_sha256"] != content_hash:
                raise SnapshotError("candidate_changed")
        for relative, value in plan["assets"].items():
            if replay.sha256_path(replay._safe(self.root, relative)) != value:
                raise SnapshotError("dataset_changed")
        for relative, value in plan["archives"].items():
            if replay.sha256_path(replay._safe(self.root, relative)) != value:
                raise SnapshotError("archive_changed")
        for split, value in plan["manifests"].items():
            if replay.sha256_path(self.root / split / "manifest.json") != value:
                raise SnapshotError("manifest_changed")
        return plan

    def _rows(self, split, cid, cancelled):
        path = self.root / split / "manifest.json"
        manifest = replay.load_manifest(path)
        policy, config = self.store.policy(cid), self._config(cid)
        archived = None
        if self.plan["mode"] == "archived_results":
            envelope = read(self.root / "archives" / f"{split}-{cid}.json")
            if (
                type(envelope) is not dict
                or set(envelope) != {"mode", "model", "results"}
                or envelope["mode"] != "archived_results"
                or envelope["model"] != self.plan["model"]
                or type(envelope["results"]) is not list
            ):
                raise SnapshotError("archive_envelope")
            archived = {}
            expected_ids = {s["sample_id"] for s in manifest["samples"]}
            for row in envelope["results"]:
                if (
                    type(row) is not dict
                    or row.get("sample_id") not in expected_ids
                    or row["sample_id"] in archived
                ):
                    raise SnapshotError("archive_sample_set")
                archived[row["sample_id"]] = row
        rows = []
        for sample in manifest["samples"]:
            binding = {
                "sample_id": sample["sample_id"],
                "manifest_sha256": manifest["manifest_sha256"],
                "candidate_fingerprint": config.fingerprint,
                "input_hash": sample["ocr_sha256"].lower(),
                "gold_sha256": sample["gold_sha256"].lower(),
                "source_hash": sample["source_hash"],
                "request_fingerprint": replay.expected_request_fingerprint(sample, config),
            }
            attempt_id = digest({"binding": binding, "plan": digest(self.plan)})
            directory = self.root / "attempts" / attempt_id
            reservation = directory / "reservation.json"
            result_path = directory / "result.json"
            if reservation.exists():
                if read(reservation) != binding:
                    raise SnapshotError("attempt_binding")
                if not result_path.exists():
                    rows.append(UnknownAttempt(attempt_id=attempt_id, **binding))
                    continue
                envelope = read(result_path)
                if digest(envelope["row"]) != envelope["sha256"]:
                    raise SnapshotError("result_corrupt")
                row = envelope["row"]
                if any(row.get(k) != v for k, v in binding.items()):
                    raise SnapshotError("result_binding")
                if row.get("status") == "unknown":
                    if set(row) != set(binding) | {
                        "status",
                        "usage",
                        "cost_cny",
                        "elapsed_seconds",
                    }:
                        raise SnapshotError("unknown_shape")
                    rows.append(UnknownAttempt(attempt_id=attempt_id, **binding))
                    continue
            else:
                if (
                    self.terminal
                    or self.unknown_elapsed
                    or cancelled()
                    or self.calls >= self.plan["max_calls"]
                    or self.spent + self.clock() - self.started >= self.plan["max_seconds"]
                ):
                    continue  # Missing stays in AG05's fixed denominator.
                if archived is not None and sample["sample_id"] not in archived:
                    continue
                atomic_create(reservation, canonical(binding))  # before execution
                self.calls += 1
                start = self.clock()
                try:
                    inp = replay.to_extraction_input(sample)
                    if archived is not None:
                        row = dict(archived[sample["sample_id"]])
                        if (
                            any(row.get(k) != v for k, v in binding.items())
                            or row.get("model") != self.plan["model"]
                            or row.get("mode") != "archived_results"
                        ):
                            raise SnapshotError("archive_source_binding")
                        validate_usage(row.get("usage"))
                        validate_cost(row.get("cost_cny"))
                        replay.validate_result_identity(manifest, sample, row, policy)
                    elif inp.category.startswith("unsupported"):
                        row = {
                            **replay._result_identity(inp, policy),
                            "status": "succeeded",
                            "review_required": True,
                            "fields": {},
                            "evidence": [],
                            "decision": {"kind": "abstain", "reason": "unsupported_case"},
                        }
                    else:
                        row = replay.extract(inp, policy)
                        # Missing required fields are domain findings, not transport failures.
                        if (
                            row.get("error_code") == "REVIEW_REQUIRED"
                            and row["status"] == "succeeded"
                        ):
                            row["domain_findings"] = [row.pop("error_code")]
                    if archived is None:
                        row = {**row, **binding, "usage": None, "cost_cny": None}
                except (TimeoutError, ConnectionError):
                    row = {**binding, "status": "unknown", "usage": None, "cost_cny": None}
                row["elapsed_seconds"] = max(0, self.clock() - start)
                atomic_create(result_path, canonical({"row": row, "sha256": digest(row)}))
                if row["status"] == "unknown":
                    rows.append(UnknownAttempt(attempt_id=attempt_id, **binding))
                    continue
            validate_usage(row.get("usage"))
            validate_cost(row.get("cost_cny"))
            if any(row.get(k) != v for k, v in binding.items()):
                raise SnapshotError("result_binding")
            replay.validate_result_identity(manifest, sample, row, policy)
            rows.append(row)
        return rows

    def _score(self, split, base_rows, candidate_rows, candidate):
        path = self.root / split / "manifest.json"
        return evaluate_samples(
            base_rows,
            candidate_rows,
            spec=EvaluationSpec(
                path,
                self.plan["manifests"][split],
                self._config(self.plan["baseline"]),
                self._config(candidate),
            ),
        )

    def run(self, *, cancelled=lambda: False):
        with exclusive_run(self.root):
            self.plan = self._validate()
            self.terminal = (self.root / "report.json").exists()
            self.started, self.spent = self.clock(), 0
            reservations = list((self.root / "attempts").glob("*/reservation.json"))
            self.calls = len(reservations)
            # A crashed call has no trustworthy elapsed receipt. Fail closed for
            # further execution rather than resetting its time budget on resume.
            self.unknown_elapsed = any(
                not (path.parent / "result.json").exists() for path in reservations
            )
            for path in (self.root / "attempts").glob("*/result.json"):
                envelope = read(path)
                if digest(envelope["row"]) != envelope["sha256"]:
                    raise SnapshotError("result_corrupt")
                validate_usage(envelope["row"].get("usage"))
                validate_cost(envelope["row"].get("cost_cny"))
                elapsed = envelope["row"].get("elapsed_seconds")
                if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                    raise SnapshotError("elapsed_invalid")
                self.spent += elapsed
            baseline = self.plan["baseline"]
            screen_base = self._rows("screen", baseline, cancelled)
            screening = {
                cid: self._score("screen", screen_base, self._rows("screen", cid, cancelled), cid)
                for cid in self.plan["candidates"]
            }
            base_rows = self._rows("dev", baseline, cancelled)
            development = {
                cid: self._score("dev", base_rows, self._rows("dev", cid, cancelled), cid)
                for cid in self.plan["candidates"]
            }
            eligible = [
                cid
                for cid, result in development.items()
                if screening[cid]["acceptance"] == "accepted_offline"
                and result["acceptance"] == "accepted_offline"
                and result["relative_result"] == "improvement"
            ]
            # Equal gains never choose arbitrarily: retain the baseline.
            selected = eligible[0] if len(eligible) == 1 else baseline
            selection = {
                "selected": selected,
                "plan_sha256": digest(self.plan),
                "development_sha256": digest(development),
                "screening_sha256": digest(screening),
                "reason": "unique_safe_improvement"
                if selected != baseline
                else "baseline_retained",
            }
            selection_path = self.root / "selection.json"
            if selection_path.exists():
                if read(selection_path) != selection:
                    raise SnapshotError("selection_changed")
            else:
                atomic_create(selection_path, canonical(selection))
            # Claim before opening holdout. A crash still counts as exposure;
            # a second run prepared concurrently cannot reuse it for selection.
            for source in self.plan["holdout_sources"]:
                exposed = self.store.root / "validation-use" / (digest(source) + ".json")
                receipt = {"plan_sha256": digest(self.plan), "selection_sha256": digest(selection)}
                try:
                    atomic_create(exposed, canonical(receipt))
                except FileExistsError:
                    if read(exposed) != receipt:
                        raise SnapshotError("holdout_exposed_by_another_experiment") from None
            base_holdout = self._rows("validation", baseline, cancelled)
            candidate_holdout = (
                base_holdout
                if selected == baseline
                else self._rows("validation", selected, cancelled)
            )
            holdout = self._score("validation", base_holdout, candidate_holdout, selected)
            complete = (
                all(r["comparable"] for r in [*screening.values(), *development.values()])
                and holdout["comparable"]
            )
            executions = []
            for path in sorted((self.root / "attempts").glob("*/reservation.json")):
                receipt_path = path.parent / "result.json"
                row = read(receipt_path)["row"] if receipt_path.exists() else {}
                executions.append(
                    {
                        **read(path),
                        "attempt_id": path.parent.name,
                        "status": row.get("status", "unknown"),
                        "usage": row.get("usage"),
                        "cost_cny": row.get("cost_cny"),
                        "elapsed_seconds": row.get("elapsed_seconds"),
                        "result_sha256": digest(row) if row else None,
                    }
                )
            report = {
                "schema_version": "rsi-experiment-report-v1",
                **selection,
                "screening": screening,
                "development": development,
                "holdout": holdout,
                "executions": executions,
                "unknown_cost_count": sum(e["cost_cny"] is None for e in executions),
                "unknown_elapsed": self.unknown_elapsed,
                "acceptance": holdout["acceptance"] if complete else "incomplete",
                "attempts": self.calls,
                "mode": self.plan["mode"],
                "model": self.plan["model"],
                "model_verified": False,
                "publication_authorized": False,
            }
            self._validate()
            final = self.root / "report.json"
            if final.exists():
                if read(final) != report:
                    raise SnapshotError("report_changed_create_new_run")
            else:
                atomic_create(final, canonical(report))
            text = "# RSI 离线比较\n\n候选已冻结；验收集不参与选优。真实模型效果未验证，不授权发布。\n\n"
            for cid, result in {**development, "独立验收": holdout}.items():
                text += f"\n## {cid}\n\n" + evaluation_report(result)["markdown"]
            markdown = self.root / "report.md"
            if markdown.exists():
                if markdown.read_bytes() != text.encode("utf-8"):
                    raise SnapshotError("markdown_changed_create_new_run")
            else:
                atomic_create(markdown, text.encode("utf-8"))
            return report
