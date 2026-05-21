import asyncio
import json

from chain.runner import run_full_chain, summarize_chain_result


async def main():
    intake = {
        "incident_date": "2026-05-10",
        "incident_location": "Colombo",
        "actor_name": "Sri Lanka Police",
        "actor_role": "police officers",
        "what_happened": (
            "The user was arrested by police officers without being shown "
            "a warrant and was not informed of the reason for the arrest. "
            "The user was detained overnight."
        ),
        "harm_suffered": (
            "The user lost personal liberty, suffered distress, and missed work."
        ),
        "user_narrative": (
            "On 10 May 2026, police officers arrested me in Colombo without "
            "showing a warrant. They did not tell me why I was arrested and "
            "kept me overnight. I missed work and suffered distress."
        ),
    }

    result = await run_full_chain(intake)

    print("\n=== SUMMARY ===")
    print(json.dumps(summarize_chain_result(result), indent=2, ensure_ascii=False))

    print("\n=== STATUS ===")
    print(result.get("status"))

    print("\n=== CONFIDENCE ===")
    print(result.get("confidence_level"))

    print("\n=== FLAGS ===")
    print(result.get("flags"))

    print("\n=== ARTICLES IDENTIFIED ===")
    print(result.get("articles_identified"))

    print("\n=== SIMILAR CASE IDS ===")
    print(result.get("similar_case_ids"))

    print("\n=== FINAL ANSWER WITH DISCLAIMER ===")
    print(result.get("final_answer_with_disclaimer", "")[:3000])


if __name__ == "__main__":
    asyncio.run(main())
