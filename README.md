# Empório da Música — customer service agent

A text-message assistant ("Mestre") for Empório da Música, a fictional musical
instrument store in Campo Grande/MS. It answers customers in Brazilian
Portuguese, looks up the catalog, promotions and orders in the store's CSVs,
reads the policy manual, and declines what is outside the store's scope.

## Run it

Requirements: Python 3.11+ and an OpenAI-compatible chat endpoint with
function calling.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e .
# put the challenge files in data/ (see data/README.md)

export EMPORIO_BASE_URL="https://your-endpoint/v1"   # any OpenAI-compatible API
export EMPORIO_API_KEY="..."
export EMPORIO_MODEL="cc/claude-sonnet-4-6"          # default; see "Model"

python -m emporio --trace                 # chat in the terminal, shows tool calls
python -m emporio --session ana           # keep and resume history in sessions/ana.json
python -m emporio --today 2026-03-24T10:30   # pin the store clock (see "Dates")
```

Example transcripts are produced by running real customer messages through
the agent:

```sh
python scripts/record_conversation.py conversations/scripts/02-return-policy-on-real-order.txt --today 2026-03-24T10:30
```

## How it works

```
customer message
   → LLM with system prompt (persona + 14 grounding rules) and 9 tools
   → tool calls run in Python against in-memory tables and policy sections
   → LLM writes the reply from the tool results
```

| Tool | Source | Used for |
| --- | --- | --- |
| `search_products` | products, categories, promotions | "violões até R$1000", alternatives |
| `get_product` | products, promotions | price, stock, specs, past promotions |
| `list_categories` | categories | what the store sells |
| `list_active_promotions` | promotions | current deals |
| `get_order_status` | orders, order_items, customers | status and tracking, after identity check |
| `check_return_options` | orders + policy windows | return/exchange/warranty eligibility today |
| `get_policy` | policy PDF | hours, address, payment, returns, shipping, warranty |
| `store_status_now` | clock + section 2 | "are you open now?" |
| `installment_options` | section 3.1 | valid installment plans and PIX price |

### Technical decisions

**Function calling, not RAG over everything.** The questions split cleanly
into two kinds: structured facts (price, stock, order status) and policy text.
Structured facts are exact-match lookups and filters over 64 products and 20
orders; putting them in a vector store would trade exactness for fuzziness.
Policy questions map to a handful of numbered sections. So the model decides
*which* source to consult by choosing a tool, and Python does the lookup.
The policy manual (section 7.1) says the assistant must never state price,
stock or deadlines without querying the system; tools make that rule
checkable in the transcripts.

**Policy retrieval by section, no embeddings.** The 8-page manual has a
clean `N.` / `N.N` heading structure. `policies.py` splits it into 28
sections; `get_policy` accepts a section number or topic words and returns
the exact text, scored by accent-insensitive keyword match on title and body.
At this size this is more predictable than embeddings and needs no extra
service. With a larger or messier corpus I would add embeddings and a
reranker.

**Rules that matter live in code, not in the prompt.** After the first
recorded run the model miscounted a 30-day exchange window. Date arithmetic
for returns now happens in `check_return_options`, which returns explicit
`eligible` flags per policy item. Same for installment limits
(`installment_options`) and promotion stacking (PIX discount is not offered
on promotional prices, policy 6.2).

**Order privacy.** `get_order_status` requires the order number plus the
customer's e-mail or phone and refuses to reveal anything if they do not
match (LGPD, policy section 9). The model never sees the customer table
directly.

**Data treatment** (`store.py`): prices to floats, `specs` JSON parsed,
only `is_active=1` promotions applied, inactive ones kept as
`past_promotions` so the agent can say a promotion has ended (policy 7.3),
`status` mapped to availability (`in_stock`, `out_of_stock`, `discontinued`,
`coming_soon`). Orders 3 and 20 have `total_brl` different from the sum of
current list prices; the loader records this and the agent quotes
`total_brl` as stored, never a recomputed value.

**Prompt.** Persona and tone come from policy 7.1 ("informal mas
profissional"). The rules are short imperative lines tied to policy items:
ground every number in a tool call, cite the section when applying a rule,
redirect accessory requests, decline off-topic requests in one line,
escalate complaints with the 24-business-hour return time, ask the manager
when a rule is unclear. Several rules (11–14) were added after reading the
first transcripts; see `conversations/earlier-runs/`.

**Interface and history.** Terminal chat, since the focus is the agent.
`--session NAME` persists the message list to JSON and resumes it, so a
customer can come back to the same conversation. The `Agent` class has one
method, `ask(text) -> reply`, so putting it behind an HTTP or WhatsApp
webhook is a thin wrapper.

### Model

Default: **Claude Sonnet 4.6** through an OpenAI-compatible gateway
(`EMPORIO_BASE_URL`). Any model with OpenAI-style tool calling works.

I first used Claude Haiku 4.5 for cost. Its transcripts are kept in
`conversations/earlier-runs/`. Even after the tool fixes it invented a store
address in one reply and said 12x installments "have interest" when the
policy says up to 12x interest-free with minimum values. Sonnet 4.6 followed
the grounding rules in all five scripted conversations, so it became the
default. For production I would run both models over a fixed set of
questions with expected facts and choose on that, not on five
conversations.

### Dates

The order data runs from 2025-10 to 2026-03. With today's real date every
order is long past its return windows, which makes the return examples
uninteresting. The examples therefore pin the store clock to
2026-03-24 10:30 (a Tuesday) with `--today`. The clock is always shown at
the top of each transcript.

## Example conversations

In `conversations/`, recorded with `scripts/record_conversation.py`; replies
are unedited and tool calls are shown inline.

1. `01-catalog-and-installments.md` — violões under R$1,000 in stock, then
   installments and PIX price for the Yamaha C40.
2. `02-return-policy-on-real-order.md` — **non-trivial**: a regret return on
   a real order, verified by e-mail; the 7-day window has passed, the agent
   explains why, then moves to the 90-day legal warranty and the need for a
   technical assessment before promising an exchange.
3. `03-promotion-and-out-of-stock.md` — a past Black Friday deal on an
   out-of-stock guitar; active promotions; PIX not stacking with promotions.
4. `04-out-of-scope-and-accessories.md` — accessories, a programming request,
   then address and opening status.
5. `05-order-verification.md` — order lookup with a wrong e-mail (refused),
   then the right phone.

## Data

The CSVs and the policy PDF supplied with the challenge are not committed:
the manual is marked as an internal document, and the customer table holds
names, phones and e-mails (fictional, but shaped like personal data). Copy
them into `data/` unchanged to run the agent. Transcripts quote only what the
agent said in the conversation.

## Assumptions

- "Até 3x sem valor mínimo de parcela (exceto abaixo de R$ 50,00)" is read as
  a R$50 minimum installment for 2–3x.
- The data has no receipt date. `estimated_delivery` is used as the receipt
  date for the 7-day and 90-day windows; the 30-day defect window counts from
  `order_date`, as policy 4.2 says "após a compra".
- The manual lists two phone numbers: (67) 3341-4444 (cover and company data)
  and (67) 3321-4500 (section 7). The agent uses the first, which appears
  twice. It also has two spellings of the e-mail domain; the agent may use
  either.
- No holiday calendar is in the data; `store_status_now` treats every
  weekday as a normal day and says so.

## Known limitations and next steps

- **Evaluation is five scripted conversations.** Next: a small test set of
  questions with expected facts (prices, eligibility, refusals), run on every
  prompt or model change.
- **Complaints and escalations are only promised, not recorded.** Next: a
  `register_ticket` tool writing to a queue the team reads.
- **No shipping quotes.** Freight outside the metro area depends on CEP,
  weight and size, which are not in the data; the agent can only state the
  rules.
- **Keyword search.** Fine for 64 products; misspellings and synonyms
  ("violão de aço" vs "folk") would need fuzzy matching or embeddings.
- **No streaming, no rate limiting, no auth** on the terminal interface.
- **Recommendations** come from the model's reading of descriptions and specs;
  there is no explicit recommendation logic.

## Use of AI assistants

<!-- Lucas: review and adjust this section to describe your real workflow before publishing. -->

This project was built with an AI coding agent (Pi, running Claude Opus) in
the terminal. I described the challenge, and the agent read the brief,
the CSVs and the policy PDF, wrote the code in small steps (see the commit
history), ran the scripted conversations against the real model and
reported the errors it found in them; the fixes in
`check_return_options`, the prompt rules 11–14 and the switch from Haiku to
Sonnet came from reading those transcripts. I reviewed the code,
transcripts and this README before submitting.

The store agent itself uses Claude Sonnet 4.6 as described in "Model".
