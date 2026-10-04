# Learn MeetLab

MeetLab runs browser meetings with a voice assistant and records conversations
for research. Start with the [architecture guide](architecture.md), then follow
one meeting through the code before exploring individual modules.

## A reading path

| Step | What to learn | Where to start |
| --- | --- | --- |
| 1 | Who uses the system and what each service does | [System view](architecture.md#system-view) |
| 2 | How a participant joins and a bot responds | [Runtime flow](architecture.md#runtime-flow) |
| 3 | Where the services run | [Infrastructure](architecture.md#infrastructure) |
| 4 | What persists after a meeting | [Data](architecture.md#data) |
| 5 | Why the architecture has these constraints | [Design choices](architecture.md#design-choices) |
| 6 | How to investigate behavior | [Events](event-log.md) and [performance tests](performance-tests.md) |
| 7 | How much v2 can carry, and how well | [Load test report, October 2026](load-test-report-2026-10.md) and [how load tests work](load-testing.md) |

The architecture guide describes the checked-out implementation reviewed on
2026-09-29. Historical investigations preserve earlier findings and proposals;
they are not a statement of today's deployed state.

## Run the documentation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
these commands from the repository root:

```sh
make docs
```

Open <http://127.0.0.1:8000>. Markdown changes reload automatically. This runs
the documentation independently of the meeting services and their credentials.

```sh
make docs-build
```

This checks the site with MkDocs strict mode and writes static HTML to `site/`.
To run MeetLab itself, follow the
[repository quick start](https://github.com/wwbp/MeetLab#quick-start).

## Add a diagram

The site uses Material for MkDocs with searchable Markdown, Mermaid diagrams,
SVG/PNG figures, and LaTeX math rendered as SVG by MathJax. The
[authoring guide](writing-diagrams.md) includes working examples and explains
how to include exported LaTeX/TikZ diagrams.
