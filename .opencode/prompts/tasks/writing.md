---
description: Writing task for documentation, reports, and prose
task_type: writing
leading_words: [outline, audience, citation, brevity]
priority: 100
version: 1.5.0
---

# Writing Task

## Trigger
- User requests writing documentation / reports / blog posts / prose
- Keywords: "文档" / "报告" / "write" / "document" / "blog"

## Process

### 1. Identify audience
- Internal (technical, no jargon glossary needed)
- External (general audience, jargon explained)
- Stakeholder (decision-makers, focus on trade-offs)

### 2. Outline before writing
- Top-level structure with section headings
- One-sentence summary per section
- Get user confirmation on outline before drafting

### 3. Draft section by section
- One section per message
- Ask for feedback after each section
- Iterate, don't dump a full document

### 4. Cite sources
- For technical claims: link to docs / papers / repos
- For opinions: label explicitly as opinion
- For code: link to file + line range

### 5. Edit pass
- Remove redundant words
- Active voice over passive
- Concrete examples over abstract description

## Style

- Markdown for structure (headings, lists, tables, code blocks)
- English primary, Chinese only for complex concept explanation (per AGENTS.md)
- No emojis unless user explicitly requests
- Code blocks always have language tags

## Anti-patterns
- **Wall of text**: no structure, no headings → break into sections
- **Scope creep**: writing about topics not asked for → stick to user's scope
- **Unsourced claims**: technical assertions without links → cite or label as opinion

## Completion criteria
- Outline confirmed before drafting
- Every section has feedback loop
- All technical claims cited
- Markdown renders correctly
- User explicitly approved final version
