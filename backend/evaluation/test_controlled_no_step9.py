"""
Test runner for controlled chain without Step 9.
"""

import asyncio
import json

from evaluation.variants.controlled_no_step9 import run_controlled_chain_no_step9


TEST_INTAKE = {
    "incident_date": "2026-05-16",
    "incident_location": "Kandy",
    "actor_name": "Department of Immigration Officers",
    "actor_role": "government officers",
    "what_happened": (
        "The user was detained and questioned by immigration officers for "
        "several hours without being informed of the reason and was denied "
        "access to a lawyer."
    ),
    "harm_suffered": (
        "Emotional distress, reputational harm, and loss of employment opportunity."
    ),
    "user_narrative": (
        "On 16 May 2026, immigration officers in Kandy detained me for hours "
        "without explaining why and refused to let me contact my lawyer."
    ),
}


async def main():
    result = await run_controlled_chain_no_step9(TEST_INTAKE)

    print(json.dumps(
        {
            "variant": result.get("variant"),
            "status": result.get("status"),
            "confidence_level": result.get("confidence_level"),
            "flags": result.get("flags"),
            "articles_identified": result.get("articles_identified"),
            "similar_case_ids": result.get("similar_case_ids"),
            "error": result.get("error"),
            "final_answer_preview": result.get("final_answer", "")[:500],
        },
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    asyncio.run(main())
