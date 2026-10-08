---
name: harness-writing
description: Writing policy for the research harness. Load it before you write a machine-read document (handoff, report, verdict, task, specification, tower instruction) or a cross-session message to another tower. It sets the language and the ASD-STE100 mode for each output channel.
---

# Harness writing policy

## Channels

| Channel | Language | Rules |
|---|---|---|
| Cross-session message to another tower | English | STE Strict |
| Machine-read document: handoff, report, verdict, task, specification, tower instruction | English | STE Strict |
| Reply or report to the researcher | Korean | No STE. Write clear Korean. |

## Do not change these items

- Code, commands, paths, file names, identifiers, hashes, numbers, units, and formulas.
- Quoted text and verbatim researcher text.
- Defined domain terms.

## STE Strict rules

1. Use active voice. Name the actor.
2. Write one instruction in one sentence.
3. Keep an instruction to 20 words or fewer. Keep a description to 25 words or fewer.
4. Use simple tenses and the imperative. Keep a compound tense only when it carries meaning, as in "may have failed".
5. Do not use semicolons.
6. Do not use phrasal verbs. Write "start", not "spin up". Write "remove", not "take off".
7. Use a verb for an action. Write "analyze the log", not "perform an analysis of the log".
8. Use one word for one meaning. Do not use different names for one thing.
9. Do not put more than three nouns in a noun cluster.
10. Do not omit articles, subjects, or verbs to make a sentence shorter.
11. Write one topic in one paragraph. Use six sentences or fewer in a paragraph.
12. Use a numbered list for three or more steps or conditions.
13. Do not use marketing adjectives such as "robust" or "seamless". Give the measurement.

## Keep the meaning

- Keep each hedge. "May have failed" stays "may have failed".
- Do not add a fact, a cause, or a certainty that the source does not state.
- If a short sentence loses a condition, a scope, or a number, keep the long sentence.

## How to use this skill

- Apply these rules while you write. Do not output a rewrite table. Do not announce this skill.
- The rule source is the `asd-ste100` skill in this repository. Read its `references/writing-rules.md` for details.
- Its `scripts/ste-lint.py` can check a draft. The check is optional.
