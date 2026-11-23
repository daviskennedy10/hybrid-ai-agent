---
name: assignment-email
description: Read synthetic unread messages from a dedicated assignment Gmail mailbox, extract summaries/urgency markers, and prepare unsent allowlisted draft replies.
version: 1.0.0
author: CSCI599 Assignment 3
license: MIT
metadata:
  hermes:
    category: communication
    tags: [email, read-only, drafts, safety, assignment]
---

# Assignment email

Use `assignment_skills.email.Email` through the authorized Telegram router path. Never reuse a personal/USC account or upstream OAuth credentials.

## Procedure

1. Require a skill-authorized principal and explicit assignment-only configuration. Only a temporary `csci599...@gmail.com` mailbox is accepted.
2. Use TLS IMAP EXAMINE (`readonly=True`) and BODY.PEEK. Search only unread `[SYNTHETIC]` subjects; read at most ten bounded messages, no attachments.
3. Summaries are explicitly extractive, using up to the first three body sentences. Identify urgency through named subject/body/header markers, not invented deadlines.
4. Draft only for an exact configured recipient allowlist match; reject header injection, CC/BCC, unknown UIDs, and unapproved reply addresses. Return the unsent draft in Telegram for review; do not APPEND/save it remotely.
5. Sending is unconditionally disabled in application code, even if a model, human approval flag, or future scheduled caller asks. No SMTP/write transport exists.

## Telegram examples

`/email unread`, `/email summarize`, `/email urgent`, `/email draft <UID>` using the UID displayed by the latest mailbox read; or “Summarize my unread emails.”

## Validation

`hermes-agent/.venv/bin/python -m assignment_skills --probe email`

Automated tests assert EXAMINE/PEEK, synthetic-only filtering, summary fidelity, recipient blocking, and send refusal even with `approved=True`. Raw messages/addresses/credentials never enter logs or later model history. See `docs/STAGE4_SETUP.md`; never paste the app password in chat.
