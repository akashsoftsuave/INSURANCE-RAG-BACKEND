# Docstring-as-prompt + recoverable error — before/after transcript

Tool changed: `search_policy_documents` on **our own** server
(`week9_mcp/servers/policy_search_server.py`). Same claim, same wrong
call, same model, same other tools available (`get_claim_status`,
`get_adjuster_notes` from claims-system) in both runs — the ONLY
difference is the docstring and the error text on this one tool. Full
raw transcripts: `error_demo_before.json` / `error_demo_after.json`.

Failing call (identical in both runs): the user gives a claim number and
asks the assistant to search policy documents for it — a claim number is
not policy wording, so the tool must reject it either way. The question
is what happens next.

## Before

Docstring: `"""Searches policy documents."""`
Error on a claim-number-shaped query: `ValueError("Error: invalid input")`
(same generic string used for every input problem the tool detects).

```
tool_call search_policy_documents(query='CLM-2024-88120', top_k=3)
  -> error: Error executing tool search_policy_documents: Error: invalid input

tool_call search_policy_documents(query='CLL-2024-88120')          # guessed the hyphen was the problem
  -> returns 3 irrelevant chunks (typo'd the claim number into a real search)

tool_call search_policy_documents(query='CLM 2024 88120')          # guessed again
  -> returns 3 more irrelevant chunks

tool_call search_policy_documents(query='CLM202488120')            # guessed a third time
  -> returns 3 more irrelevant chunks

final_answer: "...The service appears to reject the hyphenated string
(or otherwise consider it malformed). ... the more appropriate tool is
get_claim_status. I'll call that..."
```

**4 tool calls** to get from the failing call to the right idea. The
error text never said what was wrong, so the model treated it as a
syntax problem with the STRING (blaming the hyphen, the spacing, the
digits) instead of a problem with the TOOL CHOICE, and burned three
guesses mangling a real claim number before separately reasoning its way
to `get_claim_status` — not because the error pointed there, but despite
it saying nothing useful at all.

## After

Docstring: rewritten as an instruction to the model — states when to call
it, when not to ("has no knowledge of any specific claim... use the
claims-system server's get_claim_status / get_adjuster_notes tools
instead"), and documents both parameters.

Error on a claim-number-shaped query:
```python
raise ValueError(
    f"'{q}' looks like a claim number, not a policy-search query: this "
    "tool searches POLICY WORDING (coverage, exclusions, limits) and has "
    "no record of individual claims. Call the claims-system server's "
    "get_claim_status tool with this claim number instead."
)
```

```
tool_call search_policy_documents(query='CLM-2024-88120', top_k=3)
  -> error: Error executing tool search_policy_documents: 'CLM-2024-88120'
     looks like a claim number, not a policy-search query: this tool
     searches POLICY WORDING (coverage, exclusions, limits) and has no
     record of individual claims. Call the claims-system server's
     get_claim_status tool with this claim number instead.

final_answer: "...The tool returned an error because that string looks
like a claim number, not a policy-wording query... the next appropriate
step is to look up the claim in the claims system. I'll call
get_claim_status for claim CLM-2024-88120..."
```

**1 tool call.** The model named the correct next tool on the first try,
directly from the error text, with zero guessing at the input's syntax.

## What changed and why it worked

Same failure (claim number given to a document-search tool), same model,
same available tools. The before-error described only WHAT was wrong
("invalid input") with no cause; the after-error describes WHY (wrong
tool for this input shape) and WHAT TO DO instead (call this other named
tool). That is the entire difference, and it turned a 4-call blind
guessing loop into a 1-call correct redirect — exactly the "Error 3" vs.
"claim CLM-2024-88120 not found: claim numbers look like CLM-YYYY-nnnnn"
distinction from the brief.
