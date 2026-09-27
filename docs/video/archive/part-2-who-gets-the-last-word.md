# Part 2 — "Who gets the last word."

**Series:** Building an audit layer for AI agents (2 parts)
**Target runtime:** 7:30–8:00 (narration is about 1,200 words at about 150 wpm, with pauses for visuals)
**Style and visual system:** same as Part 1.
- **Brand graphics:** `brutalist/DESIGN.md` (white canvas, ink, red for emphasis only, ochre
  callouts, EB Garamond / Inter / JetBrains Mono, handnotes).
- **App captures:** keep the app's own palette.
- **Copy:** no emoji, no exclamation marks.
- **"Wrong":** never shown in red. Use an ink strike and a handnote ring.

**Sources:**
- `logs/RUN_LOG.md`: BG+U3 and B2+B3 (2026-09-25).
- `tests/test_filings.py` for BP.
- `web/frontend/src/views/CompareView.tsx` for U4.
- B4–B6 and U5–U9 follow the approved roadmap. They were not yet in RUN_LOG when this script
  was written, so see the fact-check sheet before recording.

---

## Production brief (for downstream refinement: not read aloud)

### What this video is for
- **Audience:** same as Part 1. Assume some viewers skipped Part 1: the cold open and 0:45 must
  work on their own.
- **Goal:** the viewer leaves believing that *when AI agents disagree, the right design is a hard
  stop plus a recorded human decision, and the machine's job is to make that decision easy and
  well-informed, not to make it.*
- **What they should remember:**
  1. The gate: the run stops and waits for a named person, with a written reason.
  2. Checks catch an agent that is wrong on its own, and heuristics are "worth a look", not
     "wrong".
  3. Every figure traces to its source, and the review reads answer-first.
- **Emotional arc:** tension (two answers, which is right?), then refusal (the machine won't
  pick), then the ritual of the decision, then widening scope (checks, sources, grades), then
  resolution (a human decides), then the thesis.
- **Callback to Part 1:** 0:45 "a vote" references Part 1's lesson. The close answers Part 1's
  final question.

### Glossary (additions to Part 1's)
| Plain term in narration | Technical term | Meaning |
|---|---|---|
| the gate / awaiting decision | `AWAITING_DECISION`, `gate_status` | The run is held until a human records a decision |
| auditor / investor | scope (JWT) | Two views of the same run; investors see less while a run is pending |
| hard rule / soft rule | hard check / heuristic | Hard: an arithmetic identity, which blocks. Soft: usually true, which only informs |
| the filing itself | inline XBRL in the 10-Q/10-K HTML | The value as tagged in the actual document |
| structured assessment | `<assessment>` block | Grade, direction, assumptions, key points, in a fixed vocabulary |
| data / assumption / weighting | divergence classification | Why two grades differ |
| superseded | append-only decisions | A newer decision on the same figure; the old one stays visible |

### Characters and entities
- **Agent A / B:** blue / orange, as in Part 1. In Chapter 4 they become **Bull / Bear**, and the
  tints stay the same.
- **The reviewer:** a person, never named on screen (use a placeholder like "J. Reviewer" in the
  decision record at 7:20). Refer to them as "they". Don't imply they are a certified auditor;
  identity is self-declared.
- **The AI that built it:** mentioned once (2:45), to make the P4 point. Don't dramatise it.

### Scene map
| Time | Beat | The one claim this scene must land | Asset |
|---|---|---|---|
| 0:00 | Cold open | The machine stops instead of picking | Pokémon cards, status chip |
| 0:45 | Three doors | Software, a third AI and a vote all fail; a named human doesn't | Door diagram |
| 1:40 | Ch. 1 | The decision ritual: choice, reason, name, no edits | Gate capture, decision stack |
| 2:45 | Investor view | Withheld on the server, not just hidden | Split-screen auditor vs investor |
| 3:25 | Ch. 2 | Hard rules gate; soft rules inform | Rules list, MSFT slip, GOOGL callout |
| 4:35 | Ch. 3 | Click a figure and see it in the filing; live lanes are real events | Popover capture, lanes |
| 5:30 | Ch. 4 | Grades, why they differ, and consensus only when clean | Assessment strips, three bins |
| 6:40 | Ch. 5 | Answer-first review; phone and export | Full-page scroll, phone, Markdown |
| 7:20 | Close | The human owns the judgment | Decided record, title |

### Assets to capture
- **App:** preview config `verification-layer`, `http://localhost:8000/app`, at 1280px and 375px.
- **Gate (1:40, 2:45):** run `ec1a3b44`.
  - **Auditor and investor reads:** fixtures `run_compare_gated_auditor.json` /
    `run_compare_gated_investor.json`.
  - **Investor view:** use the investor-scope token in the app.
  - **The run is still `AWAITING_DECISION`, deliberately.** For the 7:20 "decided" shot, either:
    - the human records a real decision first, which changes the stored record, so do it
      knowingly; or
    - use a copy on a temporary database.

    Never have an AI record it.
- **Checks (3:25):** the Microsoft slip is from a halted attempt, so it's in that run's raw output,
  not the matrix. Show it as typed text, not as a UI capture. For GOOGL use the stored B3 batch
  run. For a passing check list use `run_compare_b3_aapl.json` (run `20c538e4`).
- **Filing excerpt (4:35):** Apple's 10-Q, accession `0000320193-26-000020`, quarter ended
  2026-06-27 (see `tests/test_filings.py`).
- **Live lanes (4:35):** a live `/api/compare/stream` run, or drive the animation from
  `stream_compare_aapl_2026-09-24.txt`.
- **Chapters 4 and 5:** capture only once B4–B6 and U5–U9 are logged. Until then, use motion
  graphics built from the roadmap mock-up, labelled "illustrative".

### Accuracy guardrails
- **Don't say the system "knows" 1998 is right.** The narrator says it, as general knowledge; the
  system never does.
- **Don't say identity is verified.** It's self-declared, and the script says so on purpose. Keep
  that beat.
- **The investor withholding** is at read time, on the server. Storage still holds everything.
  Don't say "deleted" or "encrypted".
- **The MSFT 10× slip was never caught live.** The attempt failed its format check first.
  The script's "would have caught it" is the accurate form. Don't upgrade it to "caught".
- **GOOGL:** net income above operating income was in the filing too. Present it as a real
  non-operating gain, not an agent error.
- **No live gated accounting check has happened yet** (as of RUN_LOG 2026-09-25). Don't show one
  as real footage.
- **Chapter 4's grades** (BBB/BB, 8%/3%) are illustrative until a real bull/bear run exists.
- **"Nothing on this screen is animated for effect"** applies to the app. The motion graphics
  around it obviously are, so keep the line tied to the app capture.
- **Red is not "wrong"** (DESIGN.md). Use an ink strike plus a handnote ring.

### Tone and voice
- Same narrator as Part 1.
- Slightly slower in Chapter 1: the decision ritual should feel deliberate.
- The close is quiet, not triumphant.

### Pronunciation
Part 1's list, plus:
- "Pokémon" "POH-kay-mon"
- JWT "J-W-T" (avoid saying it at all)

### Open decisions for the refiner or human
- Whether to show the "Set grade" option (U8) at 5:30 or save it for a future episode.
- Chapters 4 and 5 are the least evidenced. If B4–B6 or U5–U9 slip, cut to a 30-second "what's
  next" coda. That leaves about 5:30 of runtime. To reach 6:00, give Ch. 2–3 longer visual beats
  or restore the directive-fix aside from Part 1.
- Whether the decision record at 7:20 shows a real name.

---

### 0:00 — Cold open

**VISUAL:** White canvas. Two cards slide in, tinted blue (A) and orange (B).
- **A:** *"The first Pokémon game reached North America in 1998."*
- **B:** *"…in 1996."*

The Δ between them: `2 years`.

**NARRATION:**
Two AI agents, one simple question: when did the first Pokémon game come out in North
America? One says 1998. The other says 1996.

For the record, the first one is right. Red and Blue reached North America in 1998.

*(beat)*

But watch what the system does with that. It doesn't pick a winner.

**VISUAL:** A status chip drops onto the run: `AWAITING DECISION`.

**NARRATION:**
It stops, and it waits for a person.

---

### 0:35 — Title card

**VISUAL:** EB Garamond: *Who gets the last word.*

---

### 0:45 — Why not let the machine decide

**VISUAL:** Three doors, drawn as simple outlines, labelled *The software*, *A third AI*, *A
vote*. Each one gets crossed through in ink as the narration names it.

**NARRATION:**
There are three obvious ways to settle a disagreement between two AIs.

Let the software pick, but it has no idea which year is right. That's the whole reason it's
asking.

Ask a third AI, but that's just another opinion, and often from the same kind of model
that got it wrong in the first place.

Take a vote, but two agents agreeing is exactly the failure from Part 1.

**VISUAL:** A fourth door, un-crossed, with a handnote: *a named human*.

**NARRATION:**
Our framework has a rule for this. A gate is a hard stop, and only a named person can clear
it. So when agents disagree on a figure, the run enters a state called "awaiting decision",
and it stays there until someone signs off.

---

### 1:40 — Chapter 1: The gate

**VISUAL:** Chapter card: *1. The gate*

**VISUAL:** An app screen capture of the Pokémon run, `ec1a3b44`. The decision panel expands
*in place* under the verdict; the comparison table stays visible beneath it.

**NARRATION:**
Here's what the reviewer sees. The disputed figure sits right under the verdict, with both
agents' values. They choose what happened: Agent A is right, Agent B is right, both are wrong,
it isn't really a conflict, or here's the correct value.

**VISUAL:** Zoom on the form fields in turn:
- the rationale box, with a live character counter ticking up;
- the name field, with its hint: *Recorded as entered; not verified*.

**NARRATION:**
They have to write down why. There's a minimum length, because "looks good" isn't a reason.
And they give their name.

Notice the small print, though. *Recorded as entered; not verified.* The system can't
actually confirm who you are yet, so it says that, rather than pretending it can.

**VISUAL:** A record stack. One decision card, then a second slides on top; the first dims
and gains the tag `superseded`.

**NARRATION:**
Decisions can't be edited. A later one can supersede an earlier one, but both stay on the
record, forever. That's the point of a record.

---

### 2:45 — What the investor sees

**VISUAL:** A split screen of the same run at two scopes.
- **Left, auditor:** `1998 │ 2 years │ 1996`.
- **Right, investor:** `Withheld │ — │ Withheld`, with the banner *Pending human review.*

**NARRATION:**
While a run waits, investors don't see the disputed numbers at all. Not the values, and not
the agents' conclusions. They see the figures that were agreed, and a note that the rest is
under review.

And the withholding isn't just hidden on screen. The server never sends those values to an
investor's view in the first place.

**ON SCREEN (source line):** *The AI that built this feature did not clear this gate. Run
ec1a3b44 was left for the human.*

**NARRATION:**
One more detail. The AI that built this feature never recorded a decision on that run. It
left it open. An AI clearing a human's gate is exactly what the gate exists to prevent.

---

### 3:25 — Chapter 2: Does it add up?

**VISUAL:** Chapter card: *2. Does it add up?*

**NARRATION:**
Disagreement between two agents is one signal. But an agent can be wrong on its own, with no
second opinion needed. Accounting has rules, and some of them are simply arithmetic.

**VISUAL:** Three rules stacked in mono, each with a check mark drawn in ink:
- `basic EPS ≥ diluted EPS`
- `free cash flow = operating cash flow − capital expenditure`
- `assets = liabilities + equity`

**NARRATION:**
These are hard rules. If an agent's own numbers break one, that's a real error, and it opens
the same gate.

**VISUAL:** A near-miss typed out:
- `told: revenue $82.9B`
- `wrote: revenue $828.9B`

Beneath it, a handnote: *10×*.

**NARRATION:**
In one Microsoft run, an agent was handed revenue of 82.9 billion and wrote 828.9 billion. That
attempt was thrown out anyway, for a formatting failure. But had it gone through, the check
comparing what an agent cites against the filing it was given would have caught it.

**VISUAL:** Google figures:
- net income `$112.2B`;
- operating income `$40.77B`.

An ochre callout rule, with the label *Unusual, worth a look*.

**NARRATION:**
Then there are softer rules. Net income is usually smaller than operating income, but not
always. A one-off gain can flip that.

In one Google run, net income was nearly three times operating income, and the filing itself
said so. So that rule doesn't block anything. It says "unusual, worth a look". A system that
treats every oddity as an error trains people to ignore it.

---

### 4:35 — Chapter 3: Show me where it says that

**VISUAL:** Chapter card: *3. Show me where it says that*

**VISUAL:** An app capture. A figure cell is clicked, and a popover anchors to it: Apple's 10-Q,
the row label, the column header, and the value highlighted in the filed table.

**NARRATION:**
Every figure in that table can now be traced to its source. Click it, and the system finds the
actual filing, locates the exact tagged value inside it, and shows you the row, the column, and
the number as filed, next to what the agent claimed.

For figures from a web search, you get the snippet the agent actually read. You don't get the
search engine's word for it.

**VISUAL:** Two agent lanes stream side by side, blue and orange. Live status lines read:
- *Searching: "AAPL Q3 revenue"…*
- *Thinking… 12s*

Each resolves into small citation pills. A single shared bar above both lanes: *one SEC fetch,
both agents*.

**NARRATION:**
And you can watch it happen. Each agent gets its own lane, showing what it's doing right now:
searching, thinking, citing. The one shared fetch from the SEC is drawn once, because it
happened once. Nothing on this screen is animated for effect. Every indicator is a real event
from the server.

---

### 5:30 — Chapter 4: From numbers to a judgment

**VISUAL:** Chapter card: *4. From numbers to a judgment*

**NARRATION:**
So far, everything's been about figures. But an analyst doesn't just report revenue. They make
a call. So the agents now end with a structured assessment: a grade, a direction, their key
assumptions, and up to three key points.

**VISUAL:** Two assessment strips, Bull (blue) and Bear (orange).
- **Bull:** `Grade BBB · Hold · revenue growth 8%`
- **Bear:** `Grade BB · Sell · revenue growth 3%`

The two growth assumptions are ringed in ochre.

**NARRATION:**
And when two agents land on different grades, the system asks *why*, and sorts the answer into
one of three kinds.

**VISUAL:** Three labelled bins, drawn left to right:
1. **Data:** they used different numbers or periods.
2. **Assumption:** same numbers, different assumptions.
3. **Weighting:** neither of the above, meaning a matter of emphasis.

**NARRATION:**
Maybe they used different numbers. Maybe they used the same numbers but assumed different
growth. Or maybe neither explains it, and it's a matter of emphasis.

The first two can be checked mechanically. The third is labelled for what it is.

**ON SCREEN:** *Consensus is set only when the grades match and nothing fails a hard check.*

**NARRATION:**
The system will only call a consensus when both grades match and nothing broke a hard rule.
Anything else goes to the human, who now has one more option on the form: set the grade.

**VISUAL:** The raw-output fallback. An agent panel reads *This agent didn't give a structured
grade*, with a button beside it. It expands to the raw text, and the spot where the assessment
should have been is highlighted.

**NARRATION:**
And when an agent skips the assessment, the screen doesn't guess. It shows you the raw output
and exactly where the missing piece should have been.

---

### 6:40 — Chapter 5: Answer first

**VISUAL:** Chapter card: *5. Answer first*

**VISUAL:** A full compare review, scrolling top to bottom:
1. the summary headline;
2. the checklist of accounting rules;
3. the decision gate;
4. the comparison table;
5. the agents;
6. the trace, collapsed.

A handnote arrow runs down the side: *most important → least*.

**NARRATION:**
Put it all together, and the review reads in the order a person needs it. First, the answer
and whether anything fails a check. Then the decision, if there is one. Then the evidence,
figure by figure. Then the agents' own words. Then the machinery, folded away for anyone who
wants it.

**VISUAL:** A phone, in "compare mode": two narrow columns with grade, direction and three key
points each. Then a "Download review" button, and a Markdown file opening.

**NARRATION:**
It works on a phone, where the two agents' key points sit side by side. And every review can
be exported as a plain document a person can read, file, and argue with.

---

### 7:20 — Close

**VISUAL:** Return to the Pokémon run. The decision panel now shows a decision record: a name,
a time, *Agent A's figure is right*, and a rationale. The status chip changes to `DECIDED`.

**NARRATION:**
There's a line in the framework this was built on. AI made execution cheap. It did not make
judgment cheap.

Everything in these two videos is execution: fetching, reading, comparing, recomputing,
checking. The machine does all of it, fast, and shows its work.

*(beat)*

And then, at the one moment that actually matters, it stops. It hands over what it found. And
it waits for someone to decide.

**ON SCREEN:** EB Garamond: *The human owns the judgment.*

---

## Fact-check sheet (not read aloud)

| Claim | Source | Status when written |
|---|---|---|
| Pokémon run `ec1a3b44`, A 1998 / B 1996, AWAITING_DECISION; investor read withheld both years; AI did not decide | RUN_LOG 2026-09-25, BG + U3 | Logged |
| 5 decision types, rationale ≥20 chars, name self-declared, supersede-not-edit | RUN_LOG, BG + U3 | Logged |
| Hard checks and heuristics; MSFT $828.9B vs $82.9B (first attempt, halted); GOOGL NI $112.2B vs OI $40.77B | RUN_LOG, B2 + B3 | Logged |
| Filing excerpt from Apple's 10-Q; search snippets | `tests/test_filings.py` (BP) | **[verify]**: code and tests present, no RUN_LOG entry yet |
| Live agent lanes, one shared SEC fetch | `CompareView.tsx` (U4) | **[verify]**: code present, no RUN_LOG entry yet |
| Structured assessment, bull/bear, divergence kinds, consensus rule, "Set grade" | Roadmap B4, B5, U7, U8 | **[verify]**: being added; check against RUN_LOG before recording |
| Answer-first review, checklist at the top, mobile compare mode, Markdown export | Roadmap U5, U7, U9, B6 | **[verify]**: being added; check against RUN_LOG before recording |
| "Bull BBB / Bear BB, 8% vs 3%" | Illustrative (from the roadmap mock-up) | Label as illustrative, or replace with a real run |
| Pokémon Red/Blue North American release, 1998 | General knowledge | Confirm before recording |
