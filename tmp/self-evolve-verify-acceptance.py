import hashlib
import json
from pathlib import Path
import sys

from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import SelfEvolveRunStatus
from aworld.self_evolve.run_history import _load_candidate_variant
from aworld.self_evolve.candidate_package import candidate_package_reference_report
from aworld.skills.compat_provider import build_compat_registry


report_path = Path(sys.argv[1]).resolve()
report = json.loads(report_path.read_text())
assert report["status"] == SelfEvolveRunStatus.SUCCEEDED.value, report["status"]
assert report["apply_policy"] == "verified_only"
assert report["gate_results"] and all(g["passed"] is True for g in report["gate_results"])
run_dir = report_path.parent
candidate_id = report["selected_candidate_id"]
assert run_dir.name == report["run_id"]
confidence = report["acceptance_confidence"]
assert confidence["passed"] is True and confidence["confidence"] == "verified"
post_apply = report["post_apply"]
assert post_apply["status"] == "accepted"
assert post_apply["published"] is False
assert post_apply["source_target_unchanged"] is True
assert post_apply["metrics"]["post_apply_passed"] is True
journal_path = Path(post_apply["journal_path"]).resolve()
assert journal_path == run_dir / "apply" / f"{candidate_id}.journal.json"
journal = json.loads(journal_path.read_text())
assert journal["status"] == "accepted"
assert journal["candidate_id"] == candidate_id
assert journal["details"]["candidate_id"] == candidate_id
assert journal["details"]["post_apply_passed"] is True
assert journal["details"]["published"] is False
verified_path = Path(post_apply["verified_target_path"]).resolve()
assert verified_path == run_dir / "verified_targets" / "agent-browser" / "SKILL.md"
assert Path(report["verified_target_path"]).resolve() == verified_path
assert verified_path == Path(journal["target_path"]).resolve()
candidate = _load_candidate_variant(run_dir / "candidates" / f"{candidate_id}.json")
assert candidate.candidate_id == candidate_id
package_check = candidate_package_reference_report(candidate, package_root=verified_path.parent)
assert package_check["materialized_file_deltas_checked"] is True
assert package_check["closed"] is True, package_check
content = verified_path.read_text()
fingerprint = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
assert fingerprint == post_apply["metrics"]["normalized_release_fingerprint"]
registry = build_compat_registry(str(verified_path.parent.parent))
descriptor = next(d for d in registry.list_descriptors() if d.skill_name == "agent-browser")
assert Path(descriptor.skill_file).resolve() == verified_path
loaded = registry.load_content(descriptor.skill_id)
metadata = loaded.raw_frontmatter["self_evolve"]
assert metadata["release_state"] == "verified"
assert metadata["verified_run_id"] == report["run_id"]
assert metadata["verified_candidate_id"] == report["selected_candidate_id"]
source = SkillTextTarget(
    Path(post_apply["source_target_path"]), target_id="agent-browser", allow_auto_apply=True
)
assert Path(post_apply["source_target_path"]).resolve() == Path(report["target"]["path"]).resolve()
assert post_apply["source_target_fingerprint_before"] == post_apply["source_target_fingerprint_after"]
assert source.fingerprint_current_content() == post_apply["source_target_fingerprint_before"]
print(json.dumps({
    "status": report["status"],
    "candidate_id": report["selected_candidate_id"],
    "confidence": confidence["confidence"],
    "gates_passed": len(report["gate_results"]),
    "post_apply_status": post_apply["status"],
    "journal_status": journal["status"],
    "verified_target_path": str(verified_path),
    "verified_content_fingerprint": fingerprint,
    "isolated_registry_load": "verified",
    "candidate_package_closed": package_check["closed"],
    "candidate_file_count": package_check["materialized_file_delta_count"],
    "source_unchanged": True,
    "published": False,
    "report_path": str(report_path),
}, indent=2))
