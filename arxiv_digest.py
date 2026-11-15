"""
arXiv Digest Bot

Fetches the newest papers from chosen arXiv categories, skips the ones already
seen, ranks the rest by keyword relevance and writes an HTML digest.
Optionally emails the digest.

Standard library only. Uses the official arXiv API (Atom feed).

Usage:
    python arxiv_digest.py                 # build today's digest
    python arxiv_digest.py --max 30        # look at the 30 newest papers
    python arxiv_digest.py --top 5         # keep the 5 best matches
    python arxiv_digest.py --all           # include papers already seen
    python arxiv_digest.py --email         # also send it (needs SMTP_* env vars)
"""

import argparse
import html
import os
import re
import smtplib
import sqlite3
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

# arXiv categories to watch (https://arxiv.org/category_taxonomy)
CATEGORIES = [
    "q-fin.TR",  # trading and market microstructure
    "q-fin.PR",  # pricing of securities
    "q-fin.CP",  # computational finance
    "q-fin.RM",  # risk management
    "q-fin.MF",  # mathematical finance
]

# A paper is kept only if its title or abstract contains one of these words.
# Whole words only, so "option" does not match "optional". Empty list = keep all.
REQUIRED_WORDS = ["option", "options"]

# Keyword -> weight. A paper's score is the sum of the weights of the keywords
# found in its title or abstract (whole words, plural allowed). Title hits count double.
KEYWORDS = {
    "option": 3,
    "volatility": 3,
    "implied volatility": 2,
    "arbitrage": 3,
    "market making": 3,
    "order book": 2,
    "liquidity": 2,
    "bitcoin": 2,
    "cryptocurrency": 2,
    "defi": 2,
    "prediction market": 3,
    "gamma": 1,
    "hedging": 1,
    "backtest": 1,
}

API_URL = "https://export.arxiv.org/api/query"
DB_PATH = Path("seen_papers.db")
OUT_DIR = Path("digests")
ATOM = {"a": "http://www.w3.org/2005/Atom"}


# =============================================================================
# FETCH
# =============================================================================

def build_query_url(categories, max_results, required=None):
    query = " OR ".join(f"cat:{c}" for c in categories)
    if required:
        words = " OR ".join(f"all:{w}" for w in required)
        query = f"({query}) AND ({words})"
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max_results,
    }
    return API_URL + "?" + urllib.parse.urlencode(params)


def fetch_feed(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "arxiv-digest-bot/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def parse_feed(xml_text):
    """Turn the Atom feed into a list of paper dicts."""
    root = ET.fromstring(xml_text)
    papers = []
    for entry in root.findall("a:entry", ATOM):
        def text(tag):
            node = entry.find(f"a:{tag}", ATOM)
            return " ".join((node.text or "").split()) if node is not None else ""

        url = text("id")
        if not url:
            continue
        papers.append({
            "id": url.rsplit("/abs/", 1)[-1],
            "url": url,
            "title": text("title"),
            "abstract": text("summary"),
            "published": text("published")[:10],
            "authors": [
                " ".join((a.findtext("a:name", "", ATOM)).split())
                for a in entry.findall("a:author", ATOM)
            ],
            "categories": [c.get("term", "") for c in entry.findall("a:category", ATOM)],
        })
    return papers


# =============================================================================
# DEDUPLICATE
# =============================================================================

def open_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, title TEXT, first_seen TEXT)"
    )
    conn.commit()
    return conn


def base_id(paper_id):
    """2401.01234v2 -> 2401.01234, so a revised paper is not sent twice."""
    head, sep, tail = paper_id.rpartition("v")
    return head if sep and tail.isdigit() else paper_id


def filter_new(conn, papers):
    return [
        p for p in papers
        if conn.execute("SELECT 1 FROM seen WHERE id = ?", (base_id(p["id"]),)).fetchone() is None
    ]


def mark_seen(conn, papers):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR IGNORE INTO seen (id, title, first_seen) VALUES (?, ?, ?)",
        [(base_id(p["id"]), p["title"], now) for p in papers],
    )
    conn.commit()


# =============================================================================
# RANK
# =============================================================================

def has_word(text, word):
    """Whole-word match, with an optional plural s."""
    return re.search(r"\b" + re.escape(word) + r"s?\b", text) is not None


def filter_required(papers, required=None):
    required = REQUIRED_WORDS if required is None else required
    if not required:
        return papers
    return [
        p for p in papers
        if any(has_word((p["title"] + " " + p["abstract"]).lower(), w) for w in required)
    ]


def score_paper(paper, keywords=KEYWORDS):
    """Return (score, matched keywords). Title hits count double."""
    title = paper["title"].lower()
    abstract = paper["abstract"].lower()
    score, matched = 0, []
    for keyword, weight in keywords.items():
        in_title = has_word(title, keyword)
        if in_title or has_word(abstract, keyword):
            score += weight * (2 if in_title else 1)
            matched.append(keyword)
    return score, matched


def rank(papers, top):
    for paper in papers:
        paper["score"], paper["matched"] = score_paper(paper)
    newest_first = sorted(papers, key=lambda p: p["published"], reverse=True)
    ranked = sorted(newest_first, key=lambda p: p["score"], reverse=True)  # stable sort
    return ranked[:top]


# =============================================================================
# DIGEST
# =============================================================================

def shorten(text, limit=600):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def build_html(papers, date_str):
    items = []
    for p in papers:
        authors = ", ".join(p["authors"][:4]) + (" et al." if len(p["authors"]) > 4 else "")
        tags = ", ".join(p["matched"]) if p["matched"] else "no keyword match"
        items.append(f"""
        <article>
          <h2><a href="{html.escape(p['url'])}">{html.escape(p['title'])}</a></h2>
          <p class="meta">{html.escape(authors)} · {html.escape(p['published'])} · {html.escape(', '.join(p['categories'][:3]))}</p>
          <p>{html.escape(shorten(p['abstract']))}</p>
          <p class="meta">Score {p['score']} · {html.escape(tags)}</p>
        </article>""")
    body = "".join(items) if items else "<p>No new papers today.</p>"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>arXiv digest {date_str}</title>
<style>
body{{font-family:system-ui,Segoe UI,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;color:#11161d;line-height:1.5}}
h1{{font-size:24px}} h2{{font-size:17px;margin:0 0 4px}} a{{color:#2a5fb0}}
article{{border-top:1px solid #e3e7ec;padding:16px 0}}
.meta{{color:#6b7684;font-size:13px;margin:4px 0}}
</style></head><body>
<h1>arXiv digest · {date_str}</h1>
<p class="meta">{len(papers)} papers, ranked by keyword relevance</p>
{body}
</body></html>"""


def save_digest(html_text, date_str, out_dir=OUT_DIR):
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{date_str}.html"
    path.write_text(html_text, encoding="utf-8")
    return path


# =============================================================================
# EMAIL (optional)
# =============================================================================

def send_email(subject, html_text):
    """Send the digest. Reads SMTP settings from environment variables."""
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    to = os.environ.get("EMAIL_TO", user)
    if not user or not password:
        print("Email skipped: set SMTP_USER and SMTP_PASS to send it.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(html_text, "html", "utf-8"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to], msg.as_string())
    return True


# =============================================================================
# MAIN
# =============================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a daily digest of new arXiv papers.")
    parser.add_argument("--max", type=int, default=50, help="newest papers to look at (default 50)")
    parser.add_argument("--top", type=int, default=10, help="papers to keep in the digest (default 10)")
    parser.add_argument("--all", action="store_true", help="include papers already seen")
    parser.add_argument("--email", action="store_true", help="also email the digest")
    args = parser.parse_args(argv)

    url = build_query_url(CATEGORIES, args.max, REQUIRED_WORDS)
    print(f"Fetching the {args.max} newest papers from {len(CATEGORIES)} categories...")
    try:
        papers = parse_feed(fetch_feed(url))
    except Exception as error:  # network or parse failure
        print(f"Could not fetch arXiv: {error}")
        return 1
    print(f"Fetched {len(papers)} papers")
    papers = filter_required(papers)
    if REQUIRED_WORDS:
        print(f"{len(papers)} mention {' / '.join(REQUIRED_WORDS)}")

    conn = open_db()
    fresh = papers if args.all else filter_new(conn, papers)
    print(f"{len(fresh)} are new")

    chosen = rank(fresh, args.top)
    date_str = datetime.now().strftime("%Y-%m-%d")
    digest = build_html(chosen, date_str)
    path = save_digest(digest, date_str)
    print(f"Digest saved to {path}")
    for p in chosen:
        print(f"  [{p['score']:>2}] {p['title'][:90]}")

    if args.email and chosen:
        if send_email(f"arXiv digest {date_str} ({len(chosen)} papers)", digest):
            print("Digest emailed")

    mark_seen(conn, fresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
