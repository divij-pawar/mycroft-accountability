# Part 1 — "Two AIs agreed. That's the problem."

**Series:** Building an audit layer for AI agents (2 parts)
**Target runtime:** 7:00–7:30 (narration is about 1,100 words at about 150 wpm, with pauses for visuals)
**Style:** Vox-style explainer. One narrator (VO), animated diagrams, real screen captures of
the app, and archival-style inserts of SEC filings.

**Visual system: `brutalist/DESIGN.md`**
- **Canvas and colour:** white canvas, ink text, red for brand and emphasis only (never to mean
  "wrong"), ochre for callout rules.
- **Type:** EB Garamond chapter cards, Inter labels, and JetBrains Mono for every number.
- **Handnotes:** Shadows Into Light for circled words and margin notes.
- **Motion:** ease-out fades and 4–8px slides only. No bounce, no zoom-on-mount.
- **Copy:** no emoji, no exclamation marks.
- **The app itself:** app screen captures keep the app's own palette (human decision,
  2026-09-24). Only the graphics around them follow DESIGN.md.

**Sources for every claim:** `logs/RUN_LOG.md`, entries 2026-09-24 → 2026-09-25 (BL, B0, B1+U2,
B2+B3).

---

## Production brief (for downstream refinement: not read aloud)

### What this video is for
- **Audience:** technically curious viewers, not specialists. They know roughly what an "AI agent"
  is. They don't know XBRL, EDGAR, EPS, or what a comparator does. Secondary audience:
  finance and audit people, who will notice any sloppiness about periods.
- **Goal:** the viewer leaves believing one idea: *two AIs agreeing is not evidence; agreement
  only counts when it is the same figure, for the same period, traced to the source.*
- **What they should remember:**
  1. The data layer was lying before any AI touched it.
  2. Comparing figures properly means metric + period + tolerance.
  3. The better comparator failed its own test, and the team said so.
- **Emotional arc:** false comfort ("they agree"), then the reveal (the number is from 2018),
  then the dig (the bug is upstream), then the build, then the humbling (the test failed), then
  earned confidence (the first real agreement), then an open question that sets up Part 2.
- **Where it sits in the series:** Part 1 is about *measuring* agreement. Part 2 is about *who
  decides* when the measurement says the agents disagree. Don't pre-empt the gate here; the
  closing only asks the question.

### The system, in one paragraph (context for the refiner)
Mycroft's *verification layer* is a Python/FastAPI service that wraps AI agents:
- **Models:** the agents are LangChain agents running local models (llama3.2 and qwen2.5 via
  Ollama), with Tavily web search.
- **Data:** for company questions they are given figures from the SEC's EDGAR "companyfacts"
  API (the structured XBRL data behind 10-Q/10-K filings).
- **Output format:** every agent answer must have a `<thought_log>` and a `<conclusion>`. If it
  doesn't, it's retried, then halted (rule "ADR-07").
- **Cross-agent validation:** runs two agents on the same subject at the same time, then
  compares their conclusions.
- **Governance:** the project follows a constitution, `SNICKERDOODLE.md`. Its key rules for this
  story:
  - P1: humans decide, machines execute.
  - P3: never invent a number.
  - P4: gates are hard stops cleared by a named human.
  - P8: label model judgments.

### Glossary (use the plain term on screen; the technical term only if needed)
| Plain term in narration | Technical term | Meaning |
|---|---|---|
| the filing data / SEC data | EDGAR companyfacts, XBRL | Machine-readable figures companies file with the SEC |
| accession number / serial number | `accn` | Unique ID of one filing |
| earnings per share | EPS (basic / diluted) | Net income divided by shares; diluted counts options etc. |
| year-to-date | YTD, nine months ended | Several quarters summed, often mistaken for one quarter |
| the comparator | contradiction rule | Code that decides whether two answers conflict |
| the old rule / the new rule | `concept_aware` / `canonical_facts` | Old: excludes figures one agent wasn't given. New: figure-by-figure |
| asset turnover | revenue ÷ total assets | How much revenue each dollar of assets produces. See the 0.693 caveat under guardrails |
| past runs we'd labelled | the labelled corpus | 16 stored runs hand-labelled as false alarms, plus 1 true alarm |

### Characters and entities
- **Agent A / Agent B:** always A = blue, B = orange in app captures. Never give them personalities
  or genders; they are "it". Don't anthropomorphise past "reads", "writes", "says".
- **The verification layer:** the narrator's "we" built it. Keep "we" for the team; never "the AI
  figured out".
- **The human:** unnamed in Part 1. (Part 2 introduces the reviewer.)

### Scene map
| Time | Beat | The one claim this scene must land | Asset |
|---|---|---|---|
| 0:00 | Cold open | Agreement on a wrong number | Motion graphic (reconstruction) |
| 0:50 | Setup | What cross-validation is; the string matcher fails both ways | Diagram; mono number pairs |
| 1:55 | Ch. 1 | The data was mislabelled before any AI touched it | Companyfacts JSON insert; table |
| 3:10 | Ch. 2 | Metric + period + tolerance | Token-split animation; matrix row |
| 3:55 | Hidden bug | The old rule hid real 5× errors | Sentence highlight; arithmetic line |
| 4:40 | Failed test | 6 vs 1, so opt-in; honesty is the feature | Two-bar chart |
| 5:40 | Ch. 3 | One table, answer between the agents; concurrency | App captures, desktop and phone |
| 6:25 | Ch. 4 | Shared figures produce the first real agreement | Filtered matrix capture |
| 6:55 | Close | Who decides? | Pokémon cards; title |

### Assets to capture (all real, all reproducible)
- **App:** run the preview config `verification-layer` (uvicorn on port 8000) and open
  `http://localhost:8000/app`. Capture at 1280px wide for desktop and 375px for phone.
- **Matrix, Microsoft MATCH row (3:10):** a stored B1 compare run. The fixture
  `web/frontend/tests/fixtures/run_compare_b1.json` is the same data. Open the run from History.
- **Filtered matrix, Apple net income / EPS MATCH (6:25):** run `20c538e4`, also in fixture
  `run_compare_b3_aapl.json`.
- **Asset-turnover runs (3:55):** stored runs `56965308` and `8c67de62`. Use their conclusion text
  verbatim for the on-screen sentence.
- **Companyfacts insert (1:55):** `tests/fixtures/edgar_aapl_companyfacts_sample.json` (trimmed real
  payload). Show the `Revenues` entry with `fy: 2018`.
- **Live stream (5:40):** `web/frontend/tests/fixtures/stream_compare_aapl_2026-09-24.txt` is a
  recorded real event stream. You can drive an animation from its timestamps.
- **Never show:** `.env`, API keys, the Tavily key, email addresses, or terminal prompts with
  user paths.

### Accuracy guardrails (things the refiner must not "improve" away)
- **The cold open is a reconstruction.** The $265.6B figure is real (stored AAPL runs), but the
  two-bubble exchange is staged. Keep the on-screen label.
- **Don't say the new rule "is better" without the caveat.** It raised more alarms. Some are
  real errors the old rule hid; five can't be judged. That nuance *is* the story.
- **Don't say the new rule "replaced" the old one.** For company runs it is opt-in; the recorded
  verdict still comes from the old rule. Generic (non-company) runs do use the new rule.
- **"39 seconds in flight together"** is overlap, not speed-up. Don't claim it made runs 2× faster;
  that was never measured.
- **Asset turnover 0.693** mixes a stale annual revenue with point-in-time assets. The point is
  *internal inconsistency* (the agent's own numbers give 0.69, it wrote 0.13), not Apple's true
  ratio. Don't say "Apple's real asset turnover is 0.69".
- **No percentages of completeness or accuracy** anywhere. None were measured (P3).
- **Red is not "wrong".** Per DESIGN.md, errors get an ink strike plus a handnote ring. Red is brand
  emphasis only.

### Tone and voice
- Measured, curious, a little dry. Vox cadence: short declaratives, a question, a beat, the
  answer.
- Admit failure plainly ("It did worse."). No hype words ("revolutionary", "game-changing"), no
  exclamation marks.
- Explain each term the first time it appears, in a clause, not a definition card.

### Pronunciation
- EDGAR "ED-gar"
- XBRL "X-B-R-L"
- EPS "E-P-S"
- 10-Q "ten-Q"
- qwen "chwen"
- Tavily "TAV-ih-lee"
- Mycroft "MY-croft"

### Open decisions for the refiner or human
- Whether to name the project (Mycroft / Snickerdoodle) on screen, or keep it generic.
- Whether to show the directive fixes (the `[/conclusion]` bracket, the dropped opening tag).
  They're cut for time; they fit as a 20-second aside in Ch. 3 if the runtime allows.
- A music bed and sound design aren't specified.
- A narrator voice isn't cast.

---

### 0:00 — Cold open

**VISUAL:** Black-on-white. Two chat bubbles slide in side by side, labelled A and B. Both
read: *"Apple's revenue was $265.6 billion."* A mono stamp lands between them: `AGREE`.
A small source line in the corner, all-caps Inter: `RECONSTRUCTION — FIGURE FROM STORED AAPL RUNS`.

**NARRATION:**
Two AI agents are asked the same question about Apple. They both come back with the same
number. They agree.

So we're done, right? Two independent answers, same result. That's how verification works.

*(beat)*

Except that number was wrong. Not a little wrong. It was Apple's revenue from 2018.

**ON SCREEN (handnote circles "2018"):** *FY2018 annual revenue*

**NARRATION:**
And both agents got it right, in a sense. They faithfully repeated exactly what they were
handed. Their agreement didn't prove the answer. It proved they had read the same mistake.

---

### 0:40 — Title card

**VISUAL:** EB Garamond, centred:
*Two AIs agreed.*
Beat. A second line fades in, smaller: *That's the problem.*

---

### 0:50 — What we built, and why

**VISUAL:** An animated diagram. A question enters at left and splits into two lanes, Agent A
and Agent B. Both lanes flow into a box labelled *Comparator*, which outputs a single light:
*agree / disagree*.

**NARRATION:**
Here's the setup. We run AI agents that read financial filings from the SEC and write up what
they find. Around them sits what we call a verification layer. It's a piece of software whose
only job is to be suspicious of the agents.

One of its tools is cross-validation. You give two agents the same company, compare what they
say, and if their numbers disagree, you raise a flag.

**VISUAL:** The comparator box opens up. Inside are two lists of bare strings side by side:
`82,886,000,000.0` and `$82.9 billion`. A red rule strikes between them. The light flips to
*disagree*.

**NARRATION:**
The first version of that comparator was, honestly, a string matcher. It pulled every number
out of both answers and checked whether the lists matched.

And that fails in both directions. It calls "eighty-two point nine billion dollars" and
"82,886,000,000" a disagreement, when they're the same number written two ways. It once
flagged an Nvidia run because one agent rated its own confidence at 90 percent. It was
treating a model's opinion of itself as a financial figure.

**ON SCREEN:** `"Confidence level: 90%"` → *not a financial figure*

**NARRATION:**
But the failure that mattered was the silent one, where the number is wrong and nobody says
anything.

---

### 1:55 — Chapter 1: The number underneath

**VISUAL:** Chapter card, EB Garamond: *1. The number underneath*

**VISUAL:** An archival-style insert. A real SEC companyfacts JSON scrolls past in mono, and a
single entry is highlighted: `"Revenues" … "fy": 2018`.

**NARRATION:**
So before touching the comparator, we went one layer down, to the data the agents are fed.

The SEC publishes every public company's reported figures as structured data. Our code asked
it a simple question: what's the latest revenue? It answered by grabbing the entry with the
latest end date and returning a bare number. It had no period and no unit.

Here's what that actually handed the agents for Apple.

**VISUAL:** A clean table builds row by row (mono numbers, ink text, ochre left rule on the
"actually" column):

| Handed to the agent | What it actually was |
|---|---|
| Revenue $265.6B | Fiscal 2018 annual revenue. Apple stopped using that tag |
| EPS 6.88 | Nine months year-to-date. The quarter was 2.02 |
| Net income $101.5B | Year-to-date. The quarter was $29.8B |

**NARRATION:**
Revenue was a six-year-old figure under a label Apple doesn't use anymore. Earnings per share
was nine months added together, presented as if it were one quarter.

And nothing in the text the agents saw said which period any of it came from. Every Apple
comparison we'd ever run had been built on this.

**VISUAL:** Each number gains a period chip: `Q3 FY2026 · 10-Q · 3 months ending 2026-06-27`.

**NARRATION:**
The fix is not glamorous. Every figure now travels with its period, its unit, the filing it came
from, and that filing's accession number, which is effectively the filing's serial number.
Quarterly figures are chosen by their actual length in days. And when a company restates a
number, the restatement wins.

That's the first lesson of this whole project. Most "AI errors" start before the AI.

---

### 3:10 — Chapter 2: Comparing figures, not strings

**VISUAL:** Chapter card: *2. Comparing figures, not strings*

**VISUAL:** A sentence from a real conclusion animates apart. *"Diluted EPS of $2.02 for Q3
FY2026"* splits into labelled tokens: **metric** `eps_diluted`, **period** `Q3 FY2026`,
**value** `2.02`.

**NARRATION:**
Now the comparator. Instead of matching strings, it reads each answer the way an analyst
would. For every number it asks three questions. What is this a figure *of*? For *which
period*? And *how precise* does a match need to be?

**VISUAL:** Three tolerance chips stack vertically:
- `EPS: to the cent`
- `Dollar figures: within 0.5%`
- `% changes: within 0.1 points`

**NARRATION:**
Earnings per share has to match to the cent. Billions of dollars get half a percent of room
for rounding. And if two agents quote the same number for *different* quarters, that isn't a
contradiction. It's shown as "different periods", and nobody gets flagged for it.

**VISUAL:** A matrix row fills in: `Revenue · — · $82,886,000,000 │ ✓ Match │ $82.9B`.

**NARRATION:**
Microsoft's 82.9 billion and 82,886,000,000 are finally the same number.

---

### 3:55 — The bug hiding inside the old fix

**VISUAL:** A sentence: *"Return on Assets of 18.85%"*. The old tagger highlights only the
word **Assets** in red and files the whole figure under `Assets`. A handnote says *wrong
drawer*.

**NARRATION:**
Then we found something uncomfortable. The previous comparator had a rule to ignore figures
that only one agent was given. It spotted the word "Assets" inside "Return on Assets", so it
quietly skipped every return-on-assets ratio.

The new one doesn't skip them. When only one agent states a ratio, it recomputes that ratio
from the agent's *own* numbers.

**VISUAL:** An arithmetic line in mono, typed out:
`revenue $265.6B ÷ assets $383.3B = 0.693`
Beneath it: `agent said: 0.13`. A handnote ring and the label *5× off*.

**NARRATION:**
Two Apple runs claimed an asset turnover of 0.13. Their own figures, in the same paragraph,
work out to 0.69. They were off by a factor of five, sitting right next to a net margin they
got right. The old system had been hiding that.

---

### 4:40 — The test it failed

**VISUAL:** Two bars on a white chart, zero baseline, ink fill, one ochre annotation.
- *Old rule:* 1 false alarm out of 16 past runs.
- *New rule:* 6 false alarms out of 16 past runs.

**NARRATION:**
So the new comparator is better. Except here's the part most product videos would cut.

Before shipping, we set a bar: replay it on sixteen past runs we'd already labelled by hand,
and it had to do no worse than the old rule. It did worse. It raised six alarms where the old
rule raised one.

Some of that gap is the old rule's blind spot. Those are the ratios it was skipping, like the
two we just saw. But for five of the new alarms, we can't say who's right. The old runs never
recorded what each agent had been given.

**ON SCREEN:** *Opt-in for company runs. The old verdict stays on record.*

**NARRATION:**
So we followed our own rule. The new comparison runs on every stored comparison, and it's
visible, but for company runs it doesn't cast the deciding vote. The verdict on record still
comes from the old rule, and the screen says which rule decided it. Switching the default is a
human decision, made when the evidence exists. Every new run now saves exactly that evidence.

---

### 5:40 — Chapter 3: Seeing it

**VISUAL:** Chapter card: *3. Seeing it*

**VISUAL:** An app screen capture of the comparison matrix, in the app's own palette. Slow push
across the columns: Figure · Period · Agent A (blue tint) · Δ · Agent B (orange tint).

**NARRATION:**
All of this lands in one table. Each figure is a row. Agent A is on one side, Agent B on the
other, and the difference sits in the middle, between them, where your eye already is.
Mismatches rise to the top.

Figures only one agent was given are folded away, so they never pass for disagreement. And the
headline is built from the rows themselves, never written by a model.

**VISUAL:** A phone frame. The same rows collapse into cards: name and period on top, then A │ Δ │ B.

**NARRATION:**
Underneath, the plumbing changed too. The server used to freeze for every other user while an
agent thought. Now both agents run at the same time. In one Apple run they were in flight
together for thirty-nine seconds, and you can watch each step arrive as it happens.

---

### 6:25 — Chapter 4: The first real agreement

**VISUAL:** Chapter card: *4. Giving them something in common*

**NARRATION:**
There was one last catch. Our two agents were deliberately given *different* slices of the
filing: one got balance-sheet figures, the other got earnings. So they rarely shared a figure,
and a comparison with nothing in common can't catch anything.

So both agents now receive two shared figures: net income and diluted earnings per share.

**VISUAL:** The matrix, filtered to two rows:
`Net income · Q3 FY2026 · $29.79B │ ✓ Match │ $29.788B`
`EPS (diluted) · Q3 FY2026 · $2.02 │ ✓ Match │ $2.02`

**NARRATION:**
And on Apple, for the first time in company mode, two agents cited the same figure, for the
same quarter, and agreed. It also matched the filing.

That's what agreement is supposed to look like. The same number, the same period, traced
back to the source.

---

### 6:55 — Close, and the hook for Part 2

**VISUAL:** Return to the cold-open bubbles. This time a Pokémon-era year sits in each:
A `1998`, B `1996`. The Δ column between them reads `2 years`.

**NARRATION:**
Which leaves the harder question. When two agents really do disagree, and one of them is
wrong, who decides? The software? A third AI? A vote?

*(beat)*

The answer we built is almost old-fashioned. That's Part 2.

**ON SCREEN:** EB Garamond, *Part 2 — Who gets the last word.*

---

## Fact-check sheet (not read aloud)

| Claim | Source |
|---|---|
| FY2018 revenue, YTD EPS 6.88 vs Q3 2.02, net income $101.5B vs $29.8B | RUN_LOG 2026-09-24, B0 |
| 82,886,000,000 vs $82.9B → MATCH; NVDA "Confidence 90%" false flag removed | RUN_LOG, B1 + U2 |
| Return on Assets mis-tagged; AAPL asset turnover 0.13 / 0.12 vs 0.693 | RUN_LOG, B1 + U2 |
| Corpus: 6 of 16 vs 1 of 16; opt-in decision | RUN_LOG, B1 + U2 |
| 39.4 s overlap on AAPL; event-loop stall fixed | RUN_LOG, BL |
| Shared net income / EPS; first same-figure MATCH (AAPL, $29.79B vs $29.788B, $2.02) | RUN_LOG, B2 + B3 |
