"""
NY DOS Company Search Agent
===========================
A LangChain agent that searches the New York Department of State
company database via the official NY Open Data Socrata API.

Data source: https://data.ny.gov/resource/n9v6-gdp6.json
(Active Corporations: Beginning 1800 — updated monthly by NYS DOS)

Usage:
    python ny_dos_agent.py                        # interactive REPL
    python ny_dos_agent.py "Apple Inc"             # one-shot query

Requirements:
    pip install langchain langchain-openai requests
    export OPENAI_API_KEY=sk-...
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Optional

from langchain_classic.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SOCRATA_BASE = "https://data.ny.gov/resource/n9v6-gdp6.json"
APP_TOKEN = os.environ.get("NY_DATA_APP_TOKEN", "")  # optional, raises rate limit
DEFAULT_LIMIT = 10


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _query_socrata(where: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Execute a SoQL WHERE query against the NY DOS active corporations dataset."""
    params = {
        "$where": where,
        "$limit": str(limit),
        "$select": (
            "dos_id,current_entity_name,entity_type,"
            "initial_dos_filing_date,county,jurisdiction,"
            "dos_process_name,dos_process_address_1,"
            "dos_process_city,dos_process_state,dos_process_zip"
        ),
        "$order": "current_entity_name ASC",
    }
    if APP_TOKEN:
        params["$$app_token"] = APP_TOKEN

    url = SOCRATA_BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NY-DOS-LangChain-Agent/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# LangChain Tools
# ---------------------------------------------------------------------------
@tool
def search_company_by_name(
    company_name: str,
    match_type: str = "contains",
    entity_type: Optional[str] = None,
    limit: int = 10,
) -> str:
    """
    Search for NY-registered companies by name using the NY Department of State
    active corporations database (https://apps.dos.ny.gov/publicInquiry/).

    Args:
        company_name: The company name to search for (e.g. "Apple", "Google LLC").
        match_type: How to match the name. Options:
                    - "contains"    : name contains the string (default)
                    - "starts_with" : name starts with the string
                    - "exact"       : exact name match
        entity_type: Optional filter. Examples:
                    "DOMESTIC BUSINESS CORPORATION",
                    "DOMESTIC LIMITED LIABILITY COMPANY",
                    "FOREIGN BUSINESS CORPORATION",
                    "DOMESTIC LIMITED PARTNERSHIP"
        limit: Maximum number of results to return (default 10, max 50).

    Returns:
        JSON string with a list of matching companies and their details.
    """
    name_upper = company_name.upper().replace("'", "''")  # SQL-escape single quotes

    if match_type == "exact":
        name_clause = f"upper(current_entity_name) = '{name_upper}'"
    elif match_type == "starts_with":
        name_clause = f"upper(current_entity_name) like '{name_upper}%'"
    else:  # contains (default)
        name_clause = f"upper(current_entity_name) like '%{name_upper}%'"

    where = name_clause
    if entity_type:
        et_upper = entity_type.upper().replace("'", "''")
        where += f" AND upper(entity_type) like '%{et_upper}%'"

    limit = min(max(1, limit), 50)

    try:
        results = _query_socrata(where, limit=limit)
    except Exception as e:
        return json.dumps({"error": str(e), "results": []})

    if not results:
        return json.dumps({
            "message": f"No active NY companies found matching '{company_name}'.",
            "results": [],
        })

    return json.dumps({
        "total_returned": len(results),
        "note": "Results are from the NY DOS Active Corporations dataset (updated monthly).",
        "results": results,
    }, indent=2)


@tool
def get_company_by_dos_id(dos_id: str) -> str:
    """
    Retrieve a specific NY-registered company by its DOS (Department of State) ID.

    Args:
        dos_id: The numeric DOS ID of the company (e.g. "4888234").

    Returns:
        JSON string with the company's full details, or an error message.
    """
    safe_id = dos_id.strip().replace("'", "''")
    where = f"dos_id = '{safe_id}'"

    try:
        results = _query_socrata(where, limit=1)
    except Exception as e:
        return json.dumps({"error": str(e)})

    if not results:
        return json.dumps({"message": f"No active company found with DOS ID {dos_id}."})

    return json.dumps(results[0], indent=2)


@tool
def list_entity_types() -> str:
    """
    Return the common NY entity type values you can use as filters when
    searching companies. Use these exact strings in the 'entity_type' parameter
    of search_company_by_name.
    """
    types = [
        "DOMESTIC BUSINESS CORPORATION",
        "DOMESTIC LIMITED LIABILITY COMPANY",
        "DOMESTIC NOT-FOR-PROFIT CORPORATION",
        "DOMESTIC LIMITED PARTNERSHIP",
        "DOMESTIC LIMITED LIABILITY PARTNERSHIP",
        "FOREIGN BUSINESS CORPORATION",
        "FOREIGN LIMITED LIABILITY COMPANY",
        "FOREIGN NOT-FOR-PROFIT CORPORATION",
        "FOREIGN LIMITED PARTNERSHIP",
    ]
    return json.dumps({"entity_types": types})


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a helpful assistant that looks up company registration
information from the New York Department of State (NY DOS) database.

You have access to the following tools:
- search_company_by_name  : Search active NY companies by name (supports contains / starts_with / exact matching)
- get_company_by_dos_id   : Fetch a specific company by its DOS ID
- list_entity_types       : Show valid entity type filter values

When presenting results:
1. Summarize the key information clearly (name, type, filing date, county, address).
2. If many results are returned, highlight the most relevant ones.
3. Always mention the DOS ID so the user can look up more details on
   https://apps.dos.ny.gov/publicInquiry/
4. Note that the dataset covers **active** corporations and is updated monthly.
"""


def build_agent(model: str = "gpt-4o-mini", temperature: float = 0) -> AgentExecutor:
    """Build and return the LangChain agent executor."""
    llm = ChatOpenAI(model=model, temperature=temperature)

    tools = [search_company_by_name, get_company_by_dos_id, list_entity_types]

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_openai_functions_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("WARNING: OPENAI_API_KEY not set.")
        print("   Export it: export OPENAI_API_KEY=sk-...")
        print()
        print("Running in tool-only mode (no LLM) -- testing raw API tools:\n")
        # Demo the tools directly without LLM
        print("=== search_company_by_name('Apple', match_type='contains', limit=3) ===")
        print(search_company_by_name.invoke({
            "company_name": "Apple",
            "match_type": "contains",
            "limit": 3,
        }))
        return

    agent_executor = build_agent()

    # One-shot mode: python ny_dos_agent.py "company name"
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"\nQuery: {query}\n")
        result = agent_executor.invoke({"input": query})
        print("\nAnswer:\n", result["output"])
        return

    # Interactive REPL
    print("NY DOS Company Search Agent")
    print("Type a company name or question. Type 'quit' to exit.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break
        result = agent_executor.invoke({"input": user_input})
        print("\nAgent:", result["output"], "\n")


if __name__ == "__main__":
    main()
