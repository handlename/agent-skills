---
name: spec-to-readable-html
description: Convert specification documents or Markdown notes into highly structured, graphical HTML reports and open them in the default browser.
---

# Spec to Readable HTML

Convert specification documents, requirements, API specs, PRDs, technical designs, or Markdown notes into beautiful, highly structured, and interactive graphical HTML documents, and open them instantly in the browser.

## Overview

Markdown specifications are highly functional for AI development but can be dense and visually exhausting for human stakeholders (product managers, QA engineers, clients). This skill enables the agent to act as a document designer, translating plain Markdown or text files into visually premium HTML reports featuring responsive sidebars, Mermaid-based interactive diagrams (with pan/zoom support), priority badges, colored callouts, and clean data grids.

### When to Trigger This Skill

Trigger this skill whenever the user:
- Asks to "convert a spec to HTML", "make a spec readable", or "display a spec graphically".
- Mentions a Zenn article or refers to the "spec-to-readable-html" skill.
- Wants a visual way to review product specs, PRDs, API schemas, database ER designs, or system flows.
- Requests a "web-based spec sheet" or "interactive design document".

---

## Instructions

Follow these instructions systematically to process a target specification file.

### 1. Resolve Target and Assets
- Locate the input file specified by the user.
- Locate the base template and component guide within the skill folder:
  - **Template HTML**: `skills/spec-to-readable-html/references/template.html`
  - **Component Guide**: `skills/spec-to-readable-html/references/html-output-template.md`
- Read both support files to understand the design tokens, visual rules, and available CSS classes.

### 2. Analyze the Specification
Determine the following metadata and structure from the source document:
- **Language**: Dynamically match the language of the conversation context or target user query. For example, if the user interacts with you in English, generate the HTML in English (`<html lang="en">`). If the user interacts in Japanese, generate it in Japanese (`<html lang="ja">`). If the conversation language is unclear, align the HTML language with the predominant language used in the input specification file itself. Keep code parameters, API endpoints, and technical terms in their original form regardless.
- **Document Type**: Is it a PRD, API Spec, ER Design, System Architecture, or QA Checklist?
- **Audience**: Mixed business and engineering (standard default).
- **Core Entities and Workflows**: Identify what diagrams are needed (Flowcharts, ERDs, Sequence Diagrams, State Diagrams). Refer to the decision guide inside the template/guide.

### 3. Generate HTML
Using the exact CSS and basic HTML structure from `skills/spec-to-readable-html/references/template.html`, build the output by replacing placeholder tokens like `{{TITLE}}`, `{{EXECUTIVE_SUMMARY}}`, etc., with analyzed contents:
- **Header**: Write a clear title, subtitle, document version, generation date, and target audience.
- **Table of Contents Sidebar**: Build anchor links corresponding to all sections (`<h2>` and `<h3>` tags) to enable fast scrolling.
- **Executive Summary**: Write a 2-paragraph overview and build the **Summary Cards grid** for quantitative facts (e.g., number of requirements, workflows, unresolved issues).
- **Glossary Grid**: Transform domain terms, abbreviations, and acronyms into card items.
- **Workflows**: Insert **Mermaid diagrams** within `<figure class="diagram-container">` blocks with zoom capability. Use flowcharts for user journeys, sequences for API flows, ERDs for databases, and state diagrams for lifecycles.
- **Requirements & Tables**: Group requirements using `.spec-table` classes. Always use priority badges (`.badge-must`, `.badge-should`, `.badge-could`) and status badges (`.badge-confirmed`, `.badge-inferred`, `.badge-assumption`).
- **Risks & Open Questions**: Display critical risks in colored cards (`.risk-card--high`, etc.) and list unanswered questions clearly using `.question-list`.
- **Directory Tree**: If the spec defines code structure, format it with `.tree-view` styling (do not wrap in `<pre>` tags).
- **Source Traceability**: Include the traceability grid in the Appendix, mapping each output section back to the source document and treatment type (Preserved, Summarized, Inferred).

### 4. Write the Output File
- Write the fully rendered, self-contained HTML buffer in a single write operation to avoid file corruption.
- Save the file in the same directory as the input spec file, with the `.html` extension (e.g., if the input is `docs/prd.md`, write `docs/prd.html`).

### 5. Start Review Server & Open Browser
- Launch the background feedback server `spec-server.py` on a random free port (e.g., 5500) using a command:
  ```bash
  python3 skills/spec-to-readable-html/scripts/spec-server.py path/to/generated.html 5500
  ```
- Make sure to launch it as a background task.
- Immediately open the server URL in the user's default browser so they can view and review it graphically:
  - On macOS, execute:
    ```bash
    open http://localhost:5500
    ```
- Stop calling tools and wait for the user to submit feedback. The system will automatically notify you with a message when the background server exits (indicating feedback has been received).

### 6. Process Feedback & Auto-Modify
- Once the background process terminates, locate and read the JSON feedback file saved at `path/to/generated-feedback.json`.
- Parse the comments array.
- Automatically modify the source spec Markdown file to address all the listed review comments.
- Re-generate the HTML spec file, restart the server, and notify the user of the updates! This creates a seamless, self-contained spec-review loop.

---

## Verification

Verify the output before completing the task:

1. **Accessibility and Quality Checklist**:
   - [ ] Verify that all text contrast is readable and uses semantic tags.
   - [ ] Ensure that every Mermaid block has a descriptive `<figcaption>`.
   - [ ] Confirm all assumptions or inferred specifications are explicitly marked with `Inferred` or `Assumption` badges.

2. **Server and Feedback Integration**:
   - [ ] Confirm the background server runs on a free port and serves the document.
   - [ ] Verify the HTML contains the review toggle button and the floating panel at the bottom right.
   - [ ] Ensure feedback is parsed and applied back to the spec file automatically.
