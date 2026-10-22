from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.validate_benchmark_reports import validate_reports, validate_extraction_report
from scripts.validate_benchmark_reports import _sample_evidence, REQUIRED_SLICES, REQUIRED_PLATFORMS


class ValidateBenchmarkReportsTestCase(unittest.TestCase):
    def test_retired_platform_rows_do_not_count_as_current_release_evidence(self):
        platforms = sorted(REQUIRED_PLATFORMS)
        rows = [{"id": f"link-{i}", "repeat": repeat,
                 "source_identity": f"{platforms[i % len(platforms)]}:content-{i}",
                 "platform": platforms[i % len(platforms)], "success": True, "complete": True}
                for repeat in range(1, 4) for i in range(60)]
        for row in rows:
            if row["id"] == "link-1":
                row["platform"] = "youtube"
        result = _sample_evidence(rows, transcription=False)
        self.assertFalse(result["valid"])
        self.assertIn("unsupported_platform_in_current_evidence", result["reasons"])

    def test_failed_raw_records_cannot_be_overridden_by_success_summary(self):
        platforms = sorted(REQUIRED_PLATFORMS)
        rows = [{"id": f"link-{i}", "repeat": repeat, "source_identity": f"{platforms[i % len(platforms)]}:content-{i}", "platform": platforms[i % len(platforms)], "success": False, "complete": False, "error_code": "platform_challenge"} for repeat in range(1,4) for i in range(60)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"report.json"
            path.write_text(json.dumps({"summary": {"release_dataset_ready": True, "success_rate": 1.0}, "samples": rows}), encoding="utf-8")
            result = validate_extraction_report(path)
        self.assertFalse(result["ready"])
        self.assertEqual(result["success_rate"], 0.0)
        self.assertIn("summary_contradicts_raw_samples", result["reasons"])
    def transcription_rows(self):
        slices = sorted(REQUIRED_SLICES)
        return [{"id": f"sample-{i}", "repeat": repeat, "audio_sha256": f"{i:064x}", "reference": "人工参考文本", "reference_kind": "human", "reference_source": f"curated-corpus/annotation-{i}", "duration_seconds": 200.0, "slice": slices[i % len(slices)], "mixed_error_rate": 0.05} for repeat in range(1,4) for i in range(72)]

    def test_raw_evidence_requires_every_sample_round_reference_and_identity(self):
        rows = self.transcription_rows()
        self.assertTrue(_sample_evidence(rows, transcription=True)["valid"])
        cases = []
        cases.append(rows[:-1])
        cases.append(rows + [rows[0]])
        missing_reference = deepcopy(rows); missing_reference[0]["reference"] = ""; cases.append(missing_reference)
        machine = deepcopy(rows); machine[0]["reference_kind"] = "model_output"; cases.append(machine)
        duplicate = deepcopy(rows)
        for row in duplicate:
            if row["id"] == "sample-1": row["audio_sha256"] = f"{0:064x}"
        cases.append(duplicate)
        derived = deepcopy(rows)
        for row in derived: row["independent_audio"] = False
        cases.append(derived)
        changed = deepcopy(rows); changed[0]["duration_seconds"] = 201.0; cases.append(changed)
        bad = deepcopy(rows); bad[0]["mixed_error_rate"] = float("nan"); cases.append(bad)
        for index, case in enumerate(cases):
            with self.subTest(index=index): self.assertFalse(_sample_evidence(case, transcription=True)["valid"])

    def test_extraction_uses_content_identity_and_all_supported_platforms(self):
        platforms = sorted(REQUIRED_PLATFORMS)
        rows = [{"id": f"link-{i}", "repeat": repeat, "source_identity": f"{platforms[i % len(platforms)]}:content-{i}", "platform": platforms[i % len(platforms)], "success": True, "complete": True} for repeat in range(1,4) for i in range(60)]
        self.assertTrue(_sample_evidence(rows, transcription=False)["valid"])
        for row in rows:
            if row["id"] == "link-1": row["source_identity"] = rows[0]["source_identity"]
        self.assertFalse(_sample_evidence(rows, transcription=False)["valid"])

    def test_reports_with_complete_raw_evidence_can_pass(self):
        platforms = sorted(REQUIRED_PLATFORMS)
        links = [{"id": f"link-{i}", "repeat": repeat, "source_identity": f"{platforms[i % len(platforms)]}:content-{i}", "platform": platforms[i % len(platforms)], "success": True, "complete": True} for repeat in range(1,4) for i in range(60)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'extraction.json').write_text(json.dumps({"summary": {"release_dataset_ready": True}, "samples": links}),encoding='utf-8')
            (root/'transcription.json').write_text(json.dumps({"runs": [{"model": "fixture", "summary": {"release_dataset_ready": True}, "samples": self.transcription_rows()}]}),encoding='utf-8')
            report = validate_reports(root/'extraction.json',root/'transcription.json')
        self.assertTrue(report['passed'])

    def test_final_evidence_requires_ready_extraction_and_transcription_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extraction = root / "extraction.json"
            transcription = root / "transcription.json"
            extraction.write_text(
                json.dumps(
                    {
                        "summary": {
                            "release_dataset_ready": True,
                            "sample_count": 60,
                            "run_count": 3,
                            "success_rate": 1.0,
                            "timeout_rate": 0.0,
                            "p95_seconds": 10.0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            transcription.write_text(
                json.dumps(
                    {
                        "runs": [
                            {
                                "model": "current",
                                "summary": {
                                    "release_dataset_ready": True,
                                    "sample_count": 72,
                                    "run_count": 3,
                                    "audio_hours": 4.0,
                                    "failure_rate": 0.0,
                                    "p95_rtf": 0.2,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = validate_reports(extraction, transcription)

        # Summary flags alone are assertions, not evidence of sixty links and
        # seventy-two human-referenced clips actually completing three rounds.
        self.assertFalse(report["passed"])

    def test_incomplete_report_cannot_pass_final_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            extraction = root / "extraction.json"
            transcription = root / "transcription.json"
            extraction.write_text(json.dumps({"summary": {"release_dataset_ready": False}}), encoding="utf-8")
            transcription.write_text(json.dumps({"runs": []}), encoding="utf-8")

            report = validate_reports(extraction, transcription)

        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
