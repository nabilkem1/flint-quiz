"""Drive a full multi-turn quiz conversation against the deployed agent.

Run::

    python -m scripts.quiz_drive

Exercises all five tools in a realistic order:

  1. ``list_topics``  — agent enumerates the catalog
  2. ``start_quiz``   — agent creates a session, returns Q1
  3. ``submit_answer`` × N — one per turn until the quiz is `done`
  4. ``get_results``  — final score breakdown

The LLM threads ``session_id`` + ``question_id`` between turns from its own
conversation context. We feed canned user messages and pick an answer
letter ("A") each round — correctness is irrelevant; the test is whether
the dispatcher loop, schemas, and Cosmos write path all hold up.
"""

from __future__ import annotations

import asyncio
import logging
import os

logging.basicConfig(level="INFO", format="%(asctime)s %(name)s %(levelname)s %(message)s")
for noisy in ("azure", "httpx", "httpcore", "urllib3", "azure.cosmos"):
    logging.getLogger(noisy).setLevel("WARNING")

logger = logging.getLogger("quiz-drive")


async def amain() -> None:
    from agent_framework import AgentSession
    from agent_framework.foundry import FoundryAgent
    from azure.identity.aio import DefaultAzureCredential

    from src.agent import chat as chat_mod
    from src.agent.dispatcher import Principal
    from src.agent.tools import ToolDeps, build_tools
    from src.data.cosmos_repository import CosmosRepository
    from src.data.question_search import QuestionSearch, build_search_client

    project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    cosmos_endpoint = os.environ["COSMOS_ENDPOINT"]
    search_endpoint = os.environ["SEARCH_ENDPOINT"]
    user_id = os.environ["CHAT_USER_ID"]

    credential = DefaultAzureCredential()
    search_client = build_search_client(
        endpoint=search_endpoint, index_name="questions", credential=credential
    )
    repo = CosmosRepository(endpoint=cosmos_endpoint, credential=credential)
    deps = ToolDeps(repo=repo, search=QuestionSearch(search_client))

    # Bind the chat.py wrapper's module globals so its tool callables
    # resolve the same deps + principal we set up here.
    chat_mod._DEPS = deps
    chat_mod._PRINCIPAL = Principal(entra_oid=user_id)
    chat_mod._TOOL_FNS = build_tools(deps)

    agent = FoundryAgent(
        project_endpoint=project_endpoint,
        agent_name=os.environ.get("AGENT_NAME", "fq-dev-agent"),
        credential=credential,
        tools=[
            chat_mod.list_topics,
            chat_mod.set_language,
            chat_mod.start_quiz,
            chat_mod.submit_answer,
            chat_mod.get_results,
        ],
    )

    session = AgentSession()  # in-memory thread; lasts only for this run

    async def turn(user_text: str) -> str:
        print(f"\nyou> {user_text}")
        resp = await agent.run(user_text, session=session)
        print(f"agent> {resp.text}\n")
        return resp.text

    # 1. start the conversation — give the agent enough context to call
    #    `start_quiz` without follow-up questions. `user_id` is no longer
    #    on the tool schema (wire-injected from the authenticated
    #    principal), so the model doesn't need / ask for it.
    await turn(
        "I'd like to take a 3-question Azure Networking quiz in English. "
        "Please start the quiz right now and show me the first question."
    )

    # 2. answer 3 questions. Pick "A" every time — we don't care about score,
    #    just that submit_answer + the question rotation behaves.
    for n in range(1, 4):
        reply = await turn(f"My answer is A.")
        if "results" in reply.lower() and "score" in reply.lower():
            # agent already produced a summary — short-circuit.
            break

    # 3. ask for results in case the agent didn't auto-summarise.
    await turn("Please show me my final results.")

    await repo.close()
    await search_client.close()
    await credential.close()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
