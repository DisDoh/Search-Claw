import unittest
from unittest.mock import Mock, patch

import searchClaw


class LlmOutputSafetyTests(unittest.TestCase):
    @patch("searchClaw.requests.post")
    def test_llm_chat_never_uses_reasoning_as_content(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": "User question: private scratchpad",
                }
            }]
        }
        post.return_value = response

        text, _ = searchClaw.llm_chat([{"role": "user", "content": "hello"}])

        self.assertEqual(text, "")

    def test_detects_observed_research_summary_leak(self):
        leaked = (
            '* User question: "how much cost a h10otti Nvidia" '
            "(implied meaning: Nvidia H100).\n"
            "* Tool result provided includes several sources."
        )

        self.assertTrue(searchClaw.looks_like_internal_reasoning(leaked))
        self.assertFalse(
            searchClaw.looks_like_internal_reasoning(
                "An Nvidia H100 generally costs tens of thousands of dollars."
            )
        )

    @patch("searchClaw.llm_chat")
    def test_final_answer_reasks_after_reasoning_leak(self, llm_chat):
        llm_chat.side_effect = [
            (
                "* User question: H100 price (implied meaning: GPU).\n"
                "* Tool result provided includes one source.\n",
                {},
            ),
            (
                "An Nvidia H100 costs about $25,000 according to this listing.\n\n"
                "Sources:\n- https://example.com/h100\n",
                {},
            ),
        ]
        results = [{
            "title": "H100 price",
            "snippet": "About $25,000",
            "url": "https://example.com/h100",
        }]

        answer = searchClaw.get_final_answer_with_reask(
            user_message="How much is an Nvidia H100?",
            tool_json='{"query":"H100 price","results":[]}',
            tool_results=results,
        )

        self.assertEqual(llm_chat.call_count, 2)
        self.assertIn("An Nvidia H100 costs about $25,000", answer)
        self.assertNotIn("User question:", answer)


if __name__ == "__main__":
    unittest.main()
