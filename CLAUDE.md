# Working agreements for this repository

## Automation: do it, do not ask

The owner has said repeatedly that they do not want to visit GitHub to perform
actions an agent can perform through the API. Treat every item below as
standing authorization. Doing these is the default; asking about them is the
exception that needs a reason.

**Always, without asking:**

* **Open a pull request** when a unit of work is finished. Base is `main`,
  head is the working branch.
* **Merge it** once CI is green. Do not park a green PR waiting for a review
  that was never requested.
* **Add or fix GitHub Actions** whenever something should run automatically —
  CI, scheduled jobs, deploy hooks. Do not describe a workflow that should
  exist; commit it.
* **Re-run, cancel or trigger workflows** as needed, and read their logs
  instead of asking what they said.
* **Push fixes** for a failing check rather than reporting the failure back.
* **Comment on a PR or issue** only when there is something a human must
  decide. Status updates belong in the chat reply, not on GitHub.

**Only stop and say something when the action is genuinely outside API reach**,
and then say it in one line with the exact click path. Known cases:

| Blocked | Why | The one-time fix |
|---|---|---|
| Repository settings toggles (auto-merge, branch protection, Pages) | Not exposed to the tooling available here | Settings → General → Pull Requests → Allow auto-merge |
| Signing up for a third-party host | Requires an account and payment details | Documented in `DEPLOYMENT.md` |
| Secrets whose values only the owner has | Cannot be read from a provider dashboard | Named explicitly with where to copy it from |

Never ask the owner to run a terminal command that a workflow could run.
If a task needs a command, put it in a workflow with `workflow_dispatch` so it
is one tap from a phone.

## Branch and release flow

* `main` is the stable branch and the PR base.
* `claude/jerry-mlb-prediction-lab-f22lok` is the working branch. Develop and
  push there.
* Finish work → push → open PR into `main` → merge when CI passes.
* The repository default branch drives both Render's blueprint deploy and the
  scheduled Actions, so do not change it without checking both still resolve.

## What CI covers

Four jobs in `.github/workflows/ci.yml`, all required to be green before merge:

| Job | Covers |
|---|---|
| `backend` | ruff, migrations apply, pytest against a real Postgres service |
| `frontend` | tsc, vitest, next build |
| `e2e` | Playwright, deliberately run with **no** API so the unavailable-state paths are exercised |
| `images` | builds both Dockerfiles, asserts they serve on a host-assigned `PORT`, that the build produced compiled CSS, and that no test toolchain reached the runtime image |

Two operational workflows, both `workflow_dispatch` so they can be triggered
from a phone: `seed.yml` (one-time database seed) and `refresh.yml` (daily at
09:15 UTC — ingest, retrain, reissue predictions, prune the raw archive).

**The database is a release asset, not a hosted service.** Every workflow
that needs it runs a Postgres service container, restores the latest dump
from the `data` release (`.github/actions/db-restore`), and — if it writes —
saves a new dump back (`.github/actions/db-save`). No `DATABASE_URL` secret
exists and none should be reintroduced; a hosted free instance expired once
and silently froze the site for three days. `backend/tests/test_data_store_workflows.py`
is the contract.

## Product rules that outrank convenience

These are the owner's constraints from the original specification. They are not
negotiable for the sake of a nicer-looking screen.

* **Never present simulated, placeholder or mock data as real data.** A missing
  provider renders `UNAVAILABLE` and names the environment variable that would
  enable it. A zero is never a stand-in for an unknown.
* **`UNAVAILABLE` and `EVEN` are different states.** "Measured and level" is a
  finding; "not measured" is the absence of one. Never collapse them.
* **Prevent leakage.** Every fact carries `knowledge_time` (when it became
  knowable) and `retrieved_at` (operational only). Feature queries filter on
  the first and never the second. This applies to display data too — standings
  shown beside a prediction are cut at that game's own first pitch.
* **No feature enters the model without evidence.** A candidate goes through
  `run_ablation` and stays only if it improves out-of-sample log loss, Brier
  score or calibration. Display context is not a feature; see
  `FEATURE_DICTIONARY.md`.
* **Never select model weights on in-sample performance.**
* **No game is a lock.** "Strong lean" is the strongest label the system emits;
  a test bans the words "lock", "guaranteed" and "sure thing".
* **Mobile first.** The product is read on an iPhone roughly nine times out of
  ten. `frontend/e2e/mobile.spec.ts` is the contract: no horizontal page scroll
  at 375px or 390px, every control at or above 44pt, sticky layers clear of one
  another, and the app installable to the home screen.

## Measure, do not assume

When a change claims a performance effect, run it and report the number. The
repository already contains negative results kept on purpose — the GBDT
ensemble in `MODELING_PLAN.md` is one. A measured "no" is a finding worth
committing, not a failure to hide.
