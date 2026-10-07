# Learn MeetLab

MeetLab runs browser meetings with a voice assistant and records conversations for research.
v2 runs on AWS through Terraform (`infra/v2`), deployed by GitHub Actions; a local Docker stack
mirrors it. Start with the architecture page, then follow a study through the guides.

## A reading path

| Step | What to learn | Where to start |
| --- | --- | --- |
| 1 | The parts, their sizes, and how much they carry | [Architecture: parts, sizes, capacity](v2-infrastructure.md) |
| 2 | Running a study: Prolific IDs, completion codes, typed chat | [Study support](study-support.md) and [session limits](session-limits.md) |
| 3 | Investigating behaviour | [Events and errors](event-log.md) |
| 4 | How much v2 carries, and how well | [Load test report, October 2026](load-test-report-2026-10.md) and [how load tests work](load-testing.md) |
| 5 | Deploying, and moving v1's data | [Deploying v2](v2-deployment.md) and [moving v1's data](v1-to-v2-migration.md) |

Decisions, costs and measurements live in `infra/v2/LEDGER.md`. v1's investigations (latency
experiments, the pilot postmortem, audits) are preserved at the
[`v1.0.0` tag](https://github.com/wwbp/MeetLab/tree/v1.0.0/docs).

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
