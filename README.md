# arXiv Digest Bot

A small Python bot that builds a daily reading list from arXiv. It fetches the newest papers in chosen categories, skips the ones it has already shown, ranks the rest by keyword relevance and writes an HTML digest. It can also email the digest.

It uses only the Python standard library and the official arXiv API, so there is nothing to install and no API key is needed.

## How it works

1. **Fetch:** one request to the arXiv API for the newest papers in the watched categories (quantitative finance by default) that mention the required words. By default these are "option" and "options", matched as whole words.
2. **Deduplicate:** a SQLite file remembers every paper already seen. A revised version of a paper (v2, v3) is not shown again.
3. **Rank:** each paper is scored by the keywords found in its title and abstract. Every keyword has a weight, and a hit in the title counts double.
4. **Digest:** the top papers go into an HTML page in `digests/`, with title, authors, date, abstract and the keywords that matched.
5. **Email (optional):** the same page is sent over SMTP.

## Run it

```bash
python arxiv_digest.py              # build today's digest
python arxiv_digest.py --max 30     # look at the 30 newest papers
python arxiv_digest.py --top 5      # keep the 5 best matches
python arxiv_digest.py --all        # include papers already seen
python arxiv_digest.py --email      # also send it by email
```

Requires Python 3.9 or newer.

## Email setup

Set these environment variables before using `--email`:

| Variable | Meaning |
|---|---|
| `SMTP_USER` | Sending address |
| `SMTP_PASS` | Password. For Gmail, an app password |
| `EMAIL_TO` | Recipient. Defaults to `SMTP_USER` |
| `SMTP_HOST`, `SMTP_PORT` | Default to `smtp.gmail.com` and `587` |

## Change what it watches

Edit `CATEGORIES`, `REQUIRED_WORDS` and `KEYWORDS` at the top of `arxiv_digest.py`. Categories use arXiv's codes, for example `q-fin.TR` or `cs.LG`.

## Tests

```bash
python -m unittest
```

The tests run offline against a sample feed and cover parsing, the query URL, the required-word filter, scoring, ranking, deduplication and the HTML output.

## Run it every day

- **Windows:** create a Task Scheduler task that runs `python arxiv_digest.py --email` each morning.
- **Linux:** add a cron line such as `0 8 * * * cd /path/to/arxiv-digest && python3 arxiv_digest.py --email`.
