# Job-source coverage audit

This is a code-path audit, not a claim that every live board is complete. The existing relevance, date, and location rules run after retrieval. Tests exercise the failure modes listed below without scraping career sites or sending mail.

| Source family | Retrieval behavior | Coverage limit or follow-up |
| --- | --- | --- |
| Greenhouse, Ashby | Published board response | No offset loop is currently required by these fetchers. Ashby now retains primary and secondary country data. A successful response alone does not prove a board slug still belongs to the intended employer. |
| Lever | Offset pages | Added bounded runtime and repeated-ID detection; retain collected jobs with an incomplete-coverage warning. |
| Workday, Amazon, Eightfold API | Keyword searches and offset pages | Existing time budgets now return explicit partial results instead of silent success. Keyword coverage is not an exhaustive company-wide crawl. |
| Rippling, Uber | Keyword searches and page counters | Added runtime bounds and explicit warnings when the budget is exhausted. API result schemas still need monitoring when employers change their sites. |
| Jibe | Numbered pages with a hard cap | Repeated IDs and exhausting the page cap now flag partial coverage. Existing short-page termination remains unchanged. |
| Google, LinkedIn | US/Canada searches, paging and cross-query deduplication | Existing repetition, malformed-response and runtime checks fail visibly. Public HTML/guest endpoints can still change or block requests. |
| iCIMS browser | Next-page control | Budget exhaustion and no-progress pagination now flag partial coverage. |
| Arm, Avature search, Eightfold browser | Per-keyword paging | Fixed global deduplication incorrectly stopping a later query when its first page overlapped an earlier query. Added/retained per-query progress checks and inner-loop budget checks. |
| Avature listing | Offset pages | Repeated pages and budget exhaustion now flag partial coverage. |
| Apple | Search pages plus individual details | Budget exhaustion is flagged; no claim of complete coverage when detail visits cannot finish. |
| Snap, Pinpoint, G-Research, Workable, Gem, Aurora | One HTML/JSON/GraphQL response | Current code assumes the response contains the intended complete listing. No new pagination contract was invented without provider evidence. |
| D. E. Shaw, Balyasny | One rendered listing | Current code assumes one-page coverage. Needs live revalidation if the company adds pagination. |
| Manual placeholders | No automated source | Excluded from successful-check counts and reported as skipped. |

## Location handling

Ashby's public posting API exposes a primary country and secondary locations. Both documented secondary-address shapes are handled. A confirmed US or Canada location keeps a role eligible even when another location is overseas. A complete all-foreign country list excludes it. Missing country information keeps the existing text fallback rather than silently discarding a potential match, and the email marks the country as unconfirmed.

Other providers retain their existing location behavior. Extending structured-country support to each provider requires checking that provider's actual schema; city names alone are not treated as confirmed countries.

Schema reference: https://developers.ashbyhq.com/docs/public-job-posting-api

## Delivery and health interaction

Partial results preserve discovered jobs and attach a coverage warning to run errors and company-health data. Three consecutive issues can therefore alert even when some jobs were retrieved. Exact-once email delivery is not guaranteed: SMTP acceptance and a GitHub state commit are separate operations. The prior reliability change serializes writers and checkpoints successful delivery to narrow that risk.
