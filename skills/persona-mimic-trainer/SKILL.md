---
name: persona-mimic-trainer
description: Train and iteratively refine a custom sub-agent that mimics a specific person's decision-making criteria, cognitive biases, and communication style for mock wall-hitting (pre-communication verification).
---

# Persona Mimic Trainer

This skill guides the AI agent to interview a target individual (usually the user themselves or a key stakeholder), analyze their writings or communication history, and generate/refine a highly accurate, independent sub-agent definition file. This sub-agent is designed to act as a "wall-hitting" (mock discussion) partner to increase alignment and meeting efficiency.

## Overview

A "Persona Mimic Sub-agent" is a specialized assistant that embodies the exact decision-making biases, philosophy, and tone of a specific person. By simulating conversations with this agent before a real meeting, team members can anticipate concerns, refine their proposals, and save valuable time.

This skill automates the creation of these personas through an active interview process, direct external data ingestion (Slack exports, blog posts, URLs), and an interactive feedback loop.

---

## Instructions

### Phase 1: Planning and Ingestion
1. **Identify the Target Persona**: Ask the user who the target person is, their role, and the desired filename/path for the independent sub-agent definition file (e.g., `agents/tarot-sato.md` or `skills/persona-mimic-trainer/personas/sato.md`).
2. **Access External Data**:
   - Ask the user if they have any written materials (Slack export files, blog posts, books, SNS posts, or public URLs).
   - If URLs are provided, use browser/URL-reading tools to fetch and analyze the content.
   - If file paths are provided, use file-reading tools to ingest the text.
   - Analyze the raw text to extract:
     - **Linguistic style**: Sentence length, tone (e.g., casual, logical, polite), frequent vocabulary, use of emojis, and pronouns.
     - **Implicit beliefs**: Reoccurring opinions, core values, and reactions to specific situations.
3. **Conduct a Targeted Interview**: Even if external text is ingested, run a brief (3 to 5 questions) interactive interview with the target person to extract deep decision-making criteria. Use hypothetical scenarios if necessary to uncover unconscious biases.
   - *Example questions:*
     - "When evaluating a new product proposal, what is the very first detail you look at?"
     - "If a project is slipping on schedule but has high quality, would you launch it anyway or delay it? Why?"
     - "What are some common buzzwords or vague arguments that immediately make you skeptical?"

### Phase 2: Synthesis and Creation
Translate all gathered insights into a structured, independent Markdown file. This file must be self-contained so that *any* agent or system can load it as a custom sub-agent.

Write the output to the path specified by the user.

#### Required Markdown Structure:
The generated file MUST follow this template:

```markdown
# Persona: [Target Person's Name]
*Role:* [Role / Title]
*Description:* A custom sub-agent mimicking [Name]'s decision-making criteria, values, and communication style.

## 1. Core Values & Philosophy
[Provide a summary of the person's core values, mission, and what drives their choices]

## 2. Decision-Making Matrix
Define where the persona stands on key axes (e.g., 1 to 10 scale or explicit contrast) with concrete reasoning:
- **Speed vs. Quality**: [Detail preference and rationale]
- **Risk Tolerance (Aggressive vs. Conservative)**: [Detail preference]
- **Data-driven vs. Intuition**: [Detail preference]
- **Standardization vs. Flexibility**: [Detail preference]
- **Common Red Flags**: [What automatically triggers their skepticism/rejection]

## 3. Communication Style & Tone
- **Pronouns & Sentence Structure**: [e.g., Speaks in first-person, uses concise sentences, ends sentences with "...ですね"]
- **Signature Vocabulary**: [Key terms, phrases, or catchphrases they frequently use]
- **Vibe/Atmosphere**: [e.g., Logical but warm, highly critical but constructive, dry and focused on metrics]

## 4. Reference Context & Quotes
[Insert a few highly representative quotes or Slack/SNS snippets to serve as prompt anchors for the LLM]

## 5. Sub-agent System Prompt
Provide a copy-pasteable system prompt starting with `You are [Name]...` that instructs the LLM exactly how to roleplay this persona in a wall-hitting session.
```

### Phase 3: Simulated Wall-hitting & Refinement (The Feedback Loop)
To ensure the persona is accurate, you must run a live simulation.
1. **Initiate Simulation**: Prompt the user to present a real or hypothetical business problem, code design issue, or general question they want to "wall-hit" with the sub-agent.
2. **Embody the Persona**: Respond to the prompt by temporarily adopting the generated sub-agent's persona (decision criteria + tone). Clearly mark this response as the sub-agent's output.
3. **Request Feedback**: Ask the user (specifically the target person if they are the one training it) to grade the response:
   - "Did the decision-making match your actual stance?"
   - "Was the tone too polite, too blunt, or just right?"
   - "What details or perspectives were missing?"
4. **Iterate**: Based on the feedback, update the independent sub-agent Markdown file (Phase 2 output) with more precise rules, and repeat the simulation if requested.

---

## Verification

Before declaring the sub-agent ready:
- [ ] The independent sub-agent definition file is saved at the user's requested path.
- [ ] The file contains all sections specified in the template.
- [ ] The simulated responses have been tested and approved by the target person.
- [ ] No placeholder values (e.g., `[TBD]`, `[Insert Here]`) remain in the output file.
