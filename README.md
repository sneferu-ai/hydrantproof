![HydrantProof — hydrant records, readiness, and district-owned water-supply worksheets](docs/showcase/banner.svg)

<p align="center">
  <a href="docs/USER_GUIDE.md"><strong>User guide</strong></a> ·
  <a href="docs/OPERATIONS.md">Installation</a> ·
  <a href="docs/API.md">API reference</a> ·
  <a href="https://ebj2525d.clients.sneferu.ai/sod/open-builds#build/1e9a956e-a115-5010-bca3-0454e5ca232f/idea-a8960dd5d1f7">The Public Works build</a>
</p>

HydrantProof brings a small fire district’s **hydrant inventory, flow-test history, inspections and water-supply worksheets** into one self-hosted application. It gives the district a shared record of what is in service, what needs attention, and how its records support a water-supply review.

Built with **[Sneferu Software on Demand](https://sneferu.ai/sod)** through Public Works: application code, interface, tests, and full product documentation are included here.

## A district-owned record

| Record | Understand | Share |
| --- | --- | --- |
| Import a hydrant inventory, record flow tests, and retain attributed inspection history. | Explore readiness, overdue work, service changes, and parcel-based coverage worksheets. | Inspect dispatch records and the evidence-pack workflow from the district’s own data. |

## Inside the repository

| Capability | Implementation |
| --- | --- |
| Registry | Hydrants, map views, CSV import and service history |
| Flow ledger | Rated-flow calculations, versioned results and superseding corrections |
| Readiness | Inspection history, deficiencies, overdue records and out-of-service tracking |
| Worksheets | Parcel coverage, credited contributions and explicit exclusion reasons |
| Stack | Python / Django, PostgreSQL, server-rendered interface; no Node build step |

## Try it locally

Use the **[operations guide](docs/OPERATIONS.md)** for the full configuration. This disposable demo uses the documented Docker image and synthetic seed data. Keep it on your own computer; the example database password is for local evaluation only.

```sh
git clone https://github.com/sneferu-ai/hydrantproof.git
cd hydrantproof
docker network create hydrantproof
docker run -d --name hydrantproof-db --network hydrantproof \
  -e POSTGRES_USER=hydrantproof -e POSTGRES_PASSWORD=hydrantproof \
  -e POSTGRES_DB=hydrantproof \
  -v hydrantproof_pgdata:/var/lib/postgresql/data postgres:16-alpine
docker build -t hydrantproof .
docker run -d --name hydrantproof --network hydrantproof \
  -p 127.0.0.1:8000:8000 \
  -e DATABASE_URL=postgres://hydrantproof:hydrantproof@hydrantproof-db:5432/hydrantproof \
  -e SEED_DEMO=true -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e ALLOW_INSECURE_LOOPBACK=true \
  -v hydrantproof_media:/app/media hydrantproof
```

Open **http://localhost:8000**. The [generated setup guide](README.generated.md) documents demo sign-in, the complete environment contract, refusal gates and test commands. Stop the demo with `docker stop hydrantproof hydrantproof-db`; the named volumes preserve its data.

## Explore the full documentation

- **[User guide](docs/USER_GUIDE.md)** — the district user’s complete walkthrough.
- **[Operations](docs/OPERATIONS.md)** — install, configure, maintain and troubleshoot.
- **[Architecture](docs/ARCHITECTURE.md)** — components, data model and design choices.
- **[API](docs/API.md)** and **[UI](docs/UI.md)** — integration and interface references.
- **[Runnable examples](docs/examples/README.md)** — flow calculations, import, API and export examples.

## Source and evaluation status

This is a **source showcase of a paused build**, not an accepted production release. The preserved app has been exercised locally, but **PDF and export defects remain open**. Do not rely on those outputs for an operational submission. No application code has been changed for this publication; the generated README remains available as [`README.generated.md`](README.generated.md).

[`SOURCE_SNAPSHOT.json`](SOURCE_SNAPSHOT.json) records the source identity and file hashes. The complete original documentation describes both the intended workflows and their limits.

HydrantProof does not predict an ISO Public Protection Classification or compute FSRS points. Its records and worksheet constants require appropriate professional review. It is not affiliated with or endorsed by ISO/Verisk or NFPA. See the [original scope and disclaimers](README.generated.md#what-it-refuses).

## License

Application source is available under the **[MIT License](LICENSE)**.

---

**From an idea to software you can inspect.** Explore [Public Works](https://ebj2525d.clients.sneferu.ai/sod/open-builds), or learn about [Sneferu Software on Demand](https://sneferu.ai/sod).
