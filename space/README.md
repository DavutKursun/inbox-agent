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
- **Limits.** 2,000 characters per email, 150 model calls and $1 per day for the whole Space, runtime state in `/tmp`
  (it resets when the Space restarts), the free Groq model by default.

Open **/ui** for the review screen and **/docs** for the API. Evaluated on 120 reviewed scenarios, including prompt
injection and impersonation; results and traces are in the repository: https://github.com/DavutKursun/inbox-agent
