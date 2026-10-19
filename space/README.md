---
title: inbox-agent
emoji: 📬
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
license: mit
pinned: false
short_description: Email agent with tools, an approval queue and an honest eval
---

# inbox-agent — a clinic inbox handled by an agent you can audit

**Nova Dental Clinic is fictional** and its 200 customers are generated data (Faker, seeded). The agent reads an email,
looks the customer up, searches a small knowledge base, finds or books appointment slots and drafts a reply.

- **Nothing is ever sent.** Replies stay drafts; there is no email, refund or payment tool.
- **The approval queue is real.** Booking and cancelling are queued, and run only after you press Approve in the UI
  (or call `/approve/{id}`); Reject leaves the calendar alone. Open the calendar expander to watch a slot appear.
- **Model and limits.** The agent runs claude-haiku-5-5, the model the evaluation numbers are for, at about $0.0015 per
  email. The whole Space shares a daily budget of $0.20 and 450 model calls (roughly 130 emails); after that Handle
  says the demo budget is used up until the next day. 2,000 characters per email; runtime state lives in `/tmp` and
  resets when the Space restarts.

Open **/ui** for the review screen and **/docs** for the API. Evaluated on 120 reviewed scenarios, including prompt
injection and impersonation; results and traces are in the repository: https://github.com/DavutKursun/inbox-agent
