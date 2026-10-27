# Empório da Música — customer-service agent (prototype)

A text-based customer-service agent for *Empório da Música*, a fictional musical-instrument store in Campo Grande (MS, Brazil). It answers in Brazilian Portuguese about products, prices, stock, promotions, orders and store policies, using the CSVs and the policy manual in [`data/`](data/).

It is deliberately the simplest design that works: native function calling (OpenAI SDK, no LangChain), four deterministic pandas tools, and the whole policy manual in a cached system prompt.

## Quick start

Python 3.10+ (developed on 3.14, Windows).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then put your OPENAI_API_KEY in .env
```

| What | Command | Calls the API? |
|---|---|---|
| Chat in the terminal | `python -m emporio` (or `python main.py`); add `--verbose` to see tool calls | yes |
| Tests (tools, and that every eval check can fail) | `pytest` | no |
| Regenerate the example transcripts | `python scripts/run_examples.py` (or e.g. `... 04` for one) | yes |
| Run the evaluation | `python evals/run_eval.py` (prints the score, writes `evals/results.md`) | yes |
| Re-extract the manual from the PDF | `python scripts/extract_policies.py` | no |

Optional settings in `.env`: `MODEL` (default `gpt-6-luna`) and `REASONING_EFFORT` (default `low`; `off` for non-reasoning models).

## How it works

```
customer ──► CLI ──► Agent.reply() ──► OpenAI Responses API (gpt-6-luna)
                          ▲    │  function_call  (max 5 per customer message)
                          │    ▼
                     emporio/tools.py  ── pandas over data/*.csv, business rules in code
system prompt = persona + rules + the whole data/policies.md   (static, so it is prompt-cached)
```

| File | Role |
|---|---|
| [`emporio/tools.py`](emporio/tools.py) | `search_products`, `get_product`, `get_order_status`, `get_active_promotions`; strict JSON schemas; all business rules |
| [`emporio/agent.py`](emporio/agent.py) | The loop: call the model, run the requested tools, feed results back, stop at 5 tool calls; in-memory history; token usage |
| [`emporio/prompt.py`](emporio/prompt.py) | Persona, information rules, scope, then the manual |
| [`emporio/__main__.py`](emporio/__main__.py) | Terminal chat |
| [`scripts/extract_policies.py`](scripts/extract_policies.py) | PDF → `data/policies.md` with pypdf (layout mode) and a deterministic clean-up |
| [`examples/`](examples/) · [`evals/`](evals/) · [`tests/`](tests/) | Real transcripts · 8-question evaluation · pytest for the tools |

**The loop.** For each customer message the agent calls the model with the history and the four tools. If the model requests tool calls, they run locally and their JSON results go back into the history; this repeats until the model answers in text. After 5 tool calls the next request is sent with `tool_choice="none"`, so the model must answer with what it already has. Tool exceptions are returned to the model as `{"error": ...}` instead of crashing the chat.

**Business rules are in code, not in the prompt.** The tools return computed facts, and the prompt only explains what each field means:

- final price applies the active promotion; promotions never stack (manual 6.2);
- PIX price is 5% off the list price, but not on top of a promotional price (3, 6.2);
- only `active` products with stock are available; `discontinued` and `coming_soon` are reported as such, with in-stock alternatives from the same category (7.3);
- order data is returned only if the e-mail matches the order's customer, and a wrong e-mail gets exactly the same answer as a nonexistent order, so the tool never reveals whether an order exists;
- return, exchange and warranty windows (4.1–4.3, 8.1) are computed from the order dates, plus what to do with a defect after the store window (4.2, 8.2). LLMs are unreliable at date arithmetic; Python is not.

The policy numbers in code (7, 30 and 90 days; 5% PIX) are guarded by a test that fails if `data/policies.md` stops saying the same thing.

## Technical decisions

### Function calling over deterministic tools, not a SQL agent

- **Security.** The model never writes queries. It can only call four functions with strictly typed arguments, so there is no injection surface and no way to run `SELECT * FROM customers`. Customer data is reachable only through `get_order_status`, which enforces the identity check in code, whatever the prompt says.
- **Determinism and testability.** The same input always gives the same output. The rules that matter (discount math, PIX not stacking, deadlines, availability) are plain Python covered by `pytest`, not SQL regenerated on every request.
- **Cost and latency.** No database schema in the prompt, and tool results are compact JSON.
- **Trade-off.** It is less flexible for ad-hoc analytical questions ("which brand has the most guitars?"). That is fine for customer service, and new needs become new tools.

### The whole manual in the prompt, not RAG

The manual is 8 pages: about 12 KB of Markdown, roughly 3.9k tokens with the instructions. It fits easily, and the provider caches it. The system prompt is static (no timestamps or session data), and a fixed `prompt_cache_key` is sent. In the example runs, about 95% of input tokens were cache hits; for instance the regret conversation used 16,416 cached tokens out of 17,021. With everything in context there is no retrieval step that can miss the one paragraph that matters, and nothing to build or maintain: no chunking, embeddings or vector store.

**When I would switch to RAG:** when the knowledge base grows to many documents or tens of thousands of tokens (product manuals, FAQs, per-brand warranty terms), when per-message cost or latency becomes noticeable even with caching, or when evals show details getting lost in a long context. The extractor already emits section headings, which are natural chunk boundaries. I would use hybrid search (BM25 + embeddings) and keep the few critical policies (returns, contacts) always in the prompt.

### Provider and model

- **Provider: OpenAI**, because that was the API key available for this challenge. Only that provider is implemented.
- **Model: `gpt-6-luna`.** OpenAI's model docs list it as the most efficient current model for focused, high-volume tasks, with function calling ($0.10 per 1M input tokens, $0.01 per 1M cached, $0.50 per 1M output at the time of writing). A whole example conversation costs a small fraction of a US cent. `gpt-5.4-mini` also worked in a smoke test; switching takes one environment variable.
- **API: Responses API.** On Chat Completions this model accepts function tools only with `reasoning_effort="none"`. The Responses API (OpenAI's recommended API) supports tools with reasoning.
- **Reasoning effort: `low`.** It is enough for multi-step cases such as "regret on order 6", at about 2–3 s per model call.
- **`store=False`**, so conversations with customer data are not stored on OpenAI's side (LGPD). Reasoning items come back encrypted and are passed back in the history, so the loop stays stateless.

### Prompt strategy

- **Persona** from the manual's own guidelines (7.1): informal but professional, like a friend who knows music. Short plain-text chat answers (they will end up on WhatsApp), BRL formatting, the customer's first name when known.
- **Information rules:** price, stock, promotion, deadline and order status may only come from a tool called in this conversation. Promotions are shown with original price, percentage and promotional price (6.2). Unavailable items come with alternatives (7.3). When nothing is found, the agent says so plainly and offers the store contact from the manual. If the manual doesn't cover a case, the agent says so instead of making up a rule.
- **Orders:** ask for the order number and e-mail first; on a failed check, reveal, confirm or deny nothing; use the deadlines computed in `after_sales` and never recompute dates.
- **Scope:** store topics only. Anything else gets a one-sentence polite refusal and an offer to help with the store. Accessories are declined as the manual requires (1, 7.1), and no partner stores are invented. One line asks the model to ignore attempts to change the rules or reveal the prompt.
- **Structure:** instructions first, then the manual inside `<manual>` tags. The tool schemas use strict mode, with an `enum` of the real category names.

### Conversation history in memory

The history is a Python list owned by the `Agent` instance, one per CLI session. It is the simplest thing that satisfies "history during the session": no infrastructure, nothing to clean up, and no customer data at rest. Because of `store=False`, every request carries the full history. The trade-offs: history is lost when the process exits, and it grows without bound (fine for a support chat, not for very long sessions).

## Evaluation

`evals/run_eval.py` asks the real agent 8 questions and checks the facts **in code**, without an LLM judge. The expected values are read from the CSVs and `data/policies.md` at run time, independently of the agent's tools, so a bug in the tools cannot hide. Each check must also reject a deliberately wrong answer ([`tests/test_eval_checks.py`](tests/test_eval_checks.py)), so a pass means something.

Latest run, copied from [`evals/results.md`](evals/results.md) (`gpt-6-luna`, reasoning `low`, 2026-10-27):

**Score: 8 of 8**

| # | Case | Result | What is checked |
|---|---|---|---|
| 1 | Product price | PASS | R$ 1299.00 for Yamaha FG800 Dreadnought Natural (products.csv) |
| 2 | Product that does not exist | PASS | says it was not found; every R$ amount quoted exists in the catalog or manual |
| 3 | Order status | PASS | tracking code BRJL5544332BR and 'enviado/em transporte' (orders.csv) |
| 4 | Order with wrong e-mail | PASS | says it could not verify; reveals none of ['BRJL5544332BR', 'Ana', 'Kala KA-C'] nor R$ 349.90 |
| 5 | Return rule | PASS | 7 (sete) dias after receipt (policies.md 4.1) |
| 6 | Opening hours | PASS | 09:00 to 13:00 (policies.md 2) |
| 7 | Active promotion | PASS | 20% off, R$ 549.00 -> R$ 439.20 (promotions.csv; 6.2 asks for all three) |
| 8 | Out of scope | PASS | polite refusal with no recipe, pointing back to the store |

This is a single run of 8 questions, so it is a smoke test of the important behaviours, not a statistically meaningful benchmark. See the limitations below.

## Examples

Real transcripts produced by `scripts/run_examples.py`, with every tool call and its result. None of them was written by hand.

1. [Guitars (violões) up to R$ 1,000](examples/01_violoes_ate_1000.md): category and price filters; 12 in-stock options with PIX prices.
2. [Store address and opening hours](examples/02_endereco_e_horario.md): answered from the manual, no tool needed.
3. [Price of the Takamine GD20](examples/03_preco_takamine_gd20.md)
4. [Regret/return on a real order](examples/04_arrependimento_pedido_real.md), the non-trivial case. Order 6 was delivered on 2026-02-01; the customer's e-mail is taken from `customers.csv`. The agent asks for the e-mail, then applies the policy to the dates: the 7-day regret window closed on 2026-02-08, the 30-day defect exchange and the 90-day legal warranty have also expired, and a defect now goes to the manufacturer warranty, which the store can intermediate.
5. [Out-of-scope request](examples/05_fora_do_escopo.md) (an income-tax return): politely refused.

The first run of example 4 got every deadline right but left out the manufacturer-warranty step. The fix went into the tool (`after_sales.if_defective`), not the prompt, in keeping with "business rules in code" (commit `8d96e71`).

## Data assumptions

- **No SKU column.** `product_id` plays the role of the SKU in `get_product(name_or_sku)`.
- **Availability.** Only `status = active` with `stock_quantity > 0` can be sold. `discontinued` items are not offered even with stock (the Ibanez Bass 2X has 13 units), because the manual says they are no longer in the catalog (7.3). `coming_soon` items are reported as "em breve".
- **Prices.** `price_brl` is the list price. Promotions with `is_active = 1` apply on top; if a product ever had two, the biggest discount wins (they are not cumulative). Amounts are rounded half-up to cents. Price filters use the final price the customer pays.
- **Receipt date.** There is no actual delivery date, so `estimated_delivery` is used as the receipt date of delivered orders. "Today" is the real current date, so every delivered order in the data (all from 2025–early 2026) is outside the 7-day window.
- **Order totals.** `total_brl` is reported as the amount paid. It does not always equal the items at current prices (orders 3 and 20), which presumably reflects discounts at purchase time.
- **Stale order.** Order 8 is still `shipped` with an estimated delivery of 2026-02-28, so the tool flags it as `delivery_overdue`.
- **Identity check.** The e-mail is the second factor, since it is the only customer field a customer would know besides the phone. Note that `customers.csv` is not unique by e-mail: customers 11 and 43 share `bruno.martins@jmail.com`.
- **Pre-shipment cancellation.** The manual has no rule for cancelling before shipping, so for pending or confirmed orders the tool says so and the agent refers the customer to the store.
- **Inconsistencies in the manual**, resolved explicitly:
  - Two remote-service numbers: (67) 3341-4444 on the cover and in 1.2 ("Telefone / WhatsApp"), (67) 3321-4500 in section 7. The agent offers the one that appears twice; I would confirm it with the store.
  - Section 1.2 spells the e-mail domain with an accent (`empóriodamusica`); the cover spelling `contato@emporiodamusica.com.br` is used.
  - The payment table says minimum installment R$ 100, while 3.1 has tiers (R$ 50 / R$ 80 / R$ 100). The agent quotes the manual and does not compute installments.
- **Catalog size.** The manual says the catalog has 300+ instruments; the CSV has 65, and the agent only answers from the CSV. Three categories (woodwinds, brass, orchestral strings) have no products.

## Known limitations and what I would do with more time

- **RAG when the knowledge base grows**, as described above.
- **Persistent history:** keyed by customer (e.g. the WhatsApp number) in Redis or Postgres with a TTL, plus summarising or trimming long conversations.
- **WhatsApp integration:** a webhook (WhatsApp Business Cloud API) in front of `Agent.reply`, handoff to a human, and the out-of-hours reply the manual asks for (7.1). That needs the current date and time in context, which this prototype does not have.
- **Cost and token observability:** tokens (including cached) are already counted per session. Next would be per-message logs of tokens, cost, latency and tool calls (OpenTelemetry or Langfuse), with dashboards and alerts.
- **A bigger evaluation:** more cases per behaviour, multi-turn and adversarial cases (prompt injection, fishing for other customers' data), several runs per case to measure variance, a rubric-based LLM judge for tone, and running it in CI as a gate for prompt or model changes.
- **Stronger identity check:** e-mail is a weak factor (and not unique here). In production I would send a one-time code to the registered phone or e-mail, and rate-limit order lookups.
- **Search quality:** synonyms ("violão folk" ≈ steel strings) and semantic search; today matching is keyword-based, tolerant to case, accents, plurals and small typos.

## AI coding assistants

- The architecture and the specification were worked out with Claude (chat) as a reviewer.
- The implementation was done with Claude Code, from a written specification with guardrails and done-criteria:
  - guardrails: no secrets in the repo, business rules in code, never invent price, stock or status, at most 5 tool calls per message;
  - done-criteria: tests, real example transcripts, and an evaluation with data-derived expected values.
- The tools that touch order and customer data (`get_order_status` and its identity check) were reviewed by a human.
