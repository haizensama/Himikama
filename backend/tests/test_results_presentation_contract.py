import unittest

from api.routes.analysis import build_main_response


class ResultsPresentationContractTests(unittest.TestCase):
    def test_completed_result_exposes_structured_layperson_fields(self):
        source = {
            "status": "complete",
            "final_answer_with_disclaimer": "Answer\n\nDISCLAIMER:\nNotice",
            "confidence_level": "high",
            "confidence": {"explanation": "Cross-checks passed."},
            "structured_assessment": {
                "final_potentially_violated_articles": ["13(1)"],
                "final_weak_or_uncertain_articles": [],
                "final_rejected_articles": ["12(1)"],
                "overall_assessment": "likely_viable",
                "precedent_alignment": "supports",
                "article_assessments": [
                    {
                        "article": "13(1)",
                        "status": "supported",
                        "reason": "The arrest may have lacked lawful grounds.",
                    }
                ],
                "key_strengths": ["The alleged actor was a police officer."],
                "key_weaknesses": ["The reason for arrest is unclear."],
            },
            "step_results": {
                "step_7": {
                    "data": {
                        "stage_b_cases": [
                            {
                                "case_id": "72",
                                "case_name": "Gunasekera v. De Fonseka",
                                "case_number": "SC APPLICATION NO. 411/71",
                                "year": 1972,
                                "judgment": "VIOLATED",
                                "articles_cited": "13(1)",
                            }
                        ]
                    }
                }
            },
        }

        response = build_main_response(attempt_id="attempt-1", source=source)
        summary = response["summary"]

        self.assertEqual(summary["overall_assessment"], "likely_viable")
        self.assertEqual(
            summary["final_potentially_violated_articles"], ["13(1)"]
        )
        self.assertEqual(
            summary["structured_assessment"]["key_strengths"],
            ["The alleged actor was a police officer."],
        )
        self.assertEqual(
            summary["similar_cases"][0]["case_name"],
            "Gunasekera v. De Fonseka",
        )
        self.assertTrue(response["reasoning_available"])

    def test_hard_gate_result_remains_a_successful_terminal_assessment(self):
        source = {
            "status": "time_barred",
            "structured_assessment": {
                "overall_assessment": "time_barred",
                "precedent_alignment": "not_assessed",
                "key_weaknesses": [
                    "The incident appears outside the 30-day filing window."
                ],
            },
        }

        response = build_main_response(attempt_id="attempt-2", source=source)

        self.assertTrue(response["success"])
        self.assertTrue(response["is_terminal"])
        self.assertEqual(response["status"], "time_barred")
        self.assertEqual(
            response["summary"]["overall_assessment"], "time_barred"
        )
        self.assertEqual(
            response["summary"]["structured_assessment"]["key_weaknesses"],
            ["The incident appears outside the 30-day filing window."],
        )

    def test_failed_attempt_is_not_presented_as_a_legal_result(self):
        response = build_main_response(
            attempt_id="attempt-3",
            source={"status": "failed", "error_code": "analysis_failed"},
        )

        self.assertFalse(response["success"])
        self.assertTrue(response["is_terminal"])
        self.assertEqual(response["error_code"], "analysis_failed")


if __name__ == "__main__":
    unittest.main()
