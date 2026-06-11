# Email draft (2026-06-11)

**Subject: Voice assistant — our own speech recognition now live in test; need your input**

Team,

- We can now run speech recognition on our own server instead of an outside vendor.
- Measured in production: **0.33s vs vendor's 0.38s** to understand you after you stop speaking; transcripts word-perfect in all test runs; audio stays in-house.
- Recognition itself takes <0.1s — the rest is a tunable "are you done talking?" pause + internet travel. Vendor's version of that knob is fixed; ours isn't.
- Capacity: one server handled 8 simultaneous conversations at 15% load.
- Cost: ~$380/month to keep on, vs vendor per-minute billing. Regular rooms still use the vendor — nothing changes for ongoing studies.

**Need from you:**

1. Cost vs speed: worth $380/mo, or keep vendor as default + ours per-study?
2. Which studies/rooms (if any) should get the fast setup?
3. Next experiments: shorter pause, other open-source models, formal accuracy study — what matters most to you: speed, accuracy, or cost?

**Try both (2 min, mic on, ping me to add the assistant):**

- Ours: http://meeting-client.eba-6bmfs2dw.us-east-1.elasticbeanstalk.com/rooms/demo-fast-ears
- Vendor: http://meeting-client.eba-6bmfs2dw.us-east-1.elasticbeanstalk.com/rooms/demo-classic-ears

Report back: feel a difference? any misheard phrase? (those become our test cases)

— Abby

*Full technical record: docs/experiment-6-gpu-stt.md*
