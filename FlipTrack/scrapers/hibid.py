# scrapers/hibid.py — reliable pagination (?apage=N) + stronger bid parsing
from __future__ import annotations
import re, time, json, html
import requests
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin, urlparse, urlsplit, urlencode, parse_qsl, urlunsplit

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_DELAY_SEC = 0.9

# ---------------- polite GET with backoff ----------------
def _retry_after_seconds(resp):
    ra = resp.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except Exception:
        return None

def polite_get(url: str, etag: str | None = None, last_modified: str | None = None,
               timeout: int = 20, retries: int = 3, backoff: float = 1.6):
    headers = dict(DEFAULT_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    attempt = 0
    while True:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 304:
            return resp, resp.headers.get("ETag"), resp.headers.get("Last-Modified")

        if resp.status_code in (429, 500, 502, 503, 504):
            if attempt >= retries:
                resp.raise_for_status()
            wait = _retry_after_seconds(resp)
            if wait is None:
                wait = min(10.0, backoff ** attempt)
            time.sleep(wait); attempt += 1; continue

        resp.raise_for_status()
        return resp, resp.headers.get("ETag"), resp.headers.get("Last-Modified")

# ---------------- soup helpers ----------------
def _clean_soup(html_text: str) -> BeautifulSoup:
    soup = BeautifulSoup(html_text, "html.parser")
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    return soup

def _text(el, sep: str = " ") -> str:
    if not el:
        return ""
    t = el.get_text(sep, strip=True)
    return re.sub(r"\s+", " ", t)

def _base_url_from_soup(soup: BeautifulSoup) -> str:
    b = soup.find("base", href=True)
    if b:
        return b["href"]
    for a in soup.find_all("a", href=True):
        try:
            u = urlparse(a["href"])
            if u.scheme and u.netloc:
                return f"{u.scheme}://{u.netloc}"
        except Exception:
            continue
    return ""

def _info_containers(soup: BeautifulSoup):
    """
    Try to narrow scope to 'Information' section if present.
    Otherwise yield the whole soup as a fallback.
    """
    candidates = []
    # common classes/ids seen on HiBid variants
    selectors = [
        "[id*=information]", "[class*=information]",
        ".lot-information", ".lotInfo", ".lot-info",
        ".lotDetails", ".lot-details", ".item-information",
    ]
    for css in selectors:
        candidates.extend(soup.select(css))

    # If there is a heading like "Information", prefer its container
    for htag in soup.find_all(["h2", "h3", "h4"]):
        if "information" in _text(htag).lower():
            # try a nearby wrapper (section/div/table)
            parent = htag.find_parent(["section","div","article"]) or htag.parent
            if parent:
                candidates.append(parent)

    # de-duplicate while preserving order
    seen, uniq = set(), []
    for el in candidates:
        if el and id(el) not in seen:
            uniq.append(el); seen.add(id(el))

    # always include soup as a last resort
    if soup not in uniq:
        uniq.append(soup)
    return uniq


def _extract_label_value_from_tables(container: BeautifulSoup, label_rx: re.Pattern) -> str | None:
    # table rows where first th/td matches label; value is in the next cell
    for table in container.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                key = _text(cells[0]).lower()
                if label_rx.search(key):
                    # preserve line breaks inside value cell (br -> \n)
                    val = cells[1].get_text("\n", strip=True)
                    val = re.sub(r"[ \t]*\n[ \t]*", "\n", val).strip()
                    if val:
                        return val
    return None


def _extract_label_value_from_dl(container: BeautifulSoup, label_rx: re.Pattern) -> str | None:
    # <dl><dt>Description</dt><dd>...</dd></dl>
    for dl in container.find_all("dl"):
        for dt in dl.find_all("dt"):
            key = _text(dt).lower()
            if label_rx.search(key):
                dd = dt.find_next_sibling("dd")
                if dd:
                    val = dd.get_text("\n", strip=True)
                    val = re.sub(r"[ \t]*\n[ \t]*", "\n", val).strip()
                    if val:
                        return val
    return None


def _extract_label_value_by_proximity(container: BeautifulSoup, label_rx: re.Pattern) -> str | None:
    # Generic: find a node whose text matches label, then take the adjacent cell/sibling
    for node in container.find_all(string=lambda s: isinstance(s, str) and label_rx.search(s.lower())):
        parent = getattr(node, "parent", None)
        if not parent:
            continue
        # sibling pattern
        sib = parent.find_next_sibling()
        if sib:
            val = sib.get_text("\n", strip=True)
            val = re.sub(r"[ \t]*\n[ \t]*", "\n", val).strip()
            if val:
                return val
        # same-parent two-column pattern
        parts = [c for c in parent.parent.find_all(recursive=False)] if parent.parent else []
        if parts and parent in parts:
            idx = parts.index(parent)
            if idx + 1 < len(parts):
                val = parts[idx + 1].get_text("\n", strip=True)
                val = re.sub(r"[ \t]*\n[ \t]*", "\n", val).strip()
                if val:
                    return val
    return None


# ---------------- pagination: ?apage=N ----------------
def _with_apage(url: str, page: int) -> str:
    """Return URL with ?apage=page (or &apage=page), replacing existing apage."""
    if page <= 1:
        # page 1 is the base catalog URL
        # also normalize by removing apage=1 if present
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "apage" in q:
            q.pop("apage", None)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
        return url
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["apage"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))

def iter_catalog_pages(catalog_url: str, *, timeout: int = 20, max_pages: int = 120):
    """
    Yield (page_number, soup) for page 1,2,3… until we stop seeing new lots on a page.
    """
    s = requests.Session()
    seen_any = False
    last_new = 0
    for p in range(1, max_pages + 1):
        url = _with_apage(catalog_url, p)
        resp = s.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code >= 400:
            break
        soup = _clean_soup(resp.text)
        yield p, soup
        # Heuristic pause between pages
        time.sleep(REQUEST_DELAY_SEC)
        # We can't measure "newness" here; the outer caller will.
        seen_any = True
        last_new = p
    # done

# ---------------- lot list discovery ----------------
def _extract_catalog_lot_links(soup: BeautifulSoup, base_url: str):
    pairs: list[tuple[str, str]] = []

    # Primary: anchor to /lot/… with nearby "Lot #123"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/lot/" in href:
            context = " ".join([_text(a), _text(a.find_parent())])[:800]
            m = re.search(r"\bLot\s*#?\s*(\d+)\b", context, re.I)
            if m:
                pairs.append((m.group(1), urljoin(base_url, href)))

    # Fallback: containers that mention "Lot #123" and have a /lot/ link inside
    if not pairs:
        for el in soup.find_all(True):
            t = _text(el)
            m = re.search(r"\bLot\s*#?\s*(\d+)\b", t, re.I)
            if m:
                a = el.find("a", href=lambda h: h and "/lot/" in h)
                if a and a.get("href"):
                    pairs.append((m.group(1), urljoin(base_url, a["href"])))

    # Dedup
    seen, uniq = set(), []
    for lot_no, url in pairs:
        if (lot_no, url) not in seen:
            uniq.append((lot_no, url)); seen.add((lot_no, url))
    return uniq

def collect_lot_map_for(catalog_url: str, target_lot_numbers: list[str] | None = None,
                        *, timeout: int = 20, max_pages: int = 120) -> dict[str, str]:
    """
    Crawl pages 1..N via ?apage=N and collect {lot_no -> absolute lot URL}.
    If target_lot_numbers is provided, stop early once all are found.
    """
    target = set([str(x).lstrip() for x in (target_lot_numbers or [])])
    # also accept de-zeroed forms (e.g., "020" == "20")
    target_nozero = set([x.lstrip("0") for x in target])

    found: dict[str, str] = {}
    visited_pairs = set()

    for page_no, soup in iter_catalog_pages(catalog_url, timeout=timeout, max_pages=max_pages):
        base_url = _base_url_from_soup(soup) or f"{urlparse(catalog_url).scheme}://{urlparse(catalog_url).netloc}"
        pairs = _extract_catalog_lot_links(soup, base_url)

        new_count = 0
        for lot_no, lot_href in pairs:
            key = lot_no
            if key not in found:
                found[key] = lot_href
                new_count += 1
            visited_pairs.add((page_no, lot_no))

        # If we were given targets, check completion and bail early.
        if target:
            if all((t in found) or (t.lstrip("0") in found) for t in target):
                break

        # If this page produced no new lots, and we've already seen at least one page with lots, assume we're done.
        if new_count == 0 and len(found) > 0:
            break

    # Normalize: if target included "020" but only "20" exists, fill it.
    if target:
        for t in target:
            if t not in found and t.lstrip("0") in found:
                found[t] = found[t.lstrip("0")]
    return found

# ---------------- lot page parsing ----------------
MONEY_RX   = re.compile(r'(?:CAD|C\$)?\s*([$€£]?\s*\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?)', re.I)
PERCENT_RX = re.compile(r'(\d+(?:\.\d+)?)\s*%')

def _parse_money_to_cents(s: str | None):
    if not s:
        return None
    # Replace NBSP and thin spaces
    s = (s or "").replace("\xa0", " ").replace("\u2009", " ")
    m = MONEY_RX.search(s)
    if not m:
        return None
    val = re.sub(r'[^\d\.]', '', m.group(1))
    if not val:
        return None
    try:
        return int(round(float(val) * 100))
    except Exception:
        return None

def _parse_percent(s: str | None):
    if not s:
        return None
    m = PERCENT_RX.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None

def _extract_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1 and _text(h1):
        return _text(h1)
    if soup.title and soup.title.string:
        return re.sub(r"\s*-\s*HiBid.*$", "", soup.title.string.strip(), flags=re.I) or None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    return None

def _extract_description(soup: BeautifulSoup) -> str | None:
    """
    Extract description with priority:
      1) 'Information' table/section where left column is 'Description'
      2) <dl>/<dt>Description</dt><dd>...</dd>
      3) Proximity-based label match inside 'Information' container
      4) Legacy fallbacks (divs with *description*, JSON-LD, OG)
    """
    label_rx = re.compile(r"\bdescription\b", re.I)

    # 1/2/3: Work inside 'information' containers first
    for cont in _info_containers(soup):
        # Exact table mapping
        val = _extract_label_value_from_tables(cont, label_rx)
        if val:
            return val

        # Definition list mapping
        val = _extract_label_value_from_dl(cont, label_rx)
        if val:
            return val

        # Generic proximity (label to the left / above)
        val = _extract_label_value_by_proximity(cont, label_rx)
        if val:
            return val

    # 4) Legacy block-level fallbacks (keep your previous behavior)
    selectors = [
        "[id*=description]", "[class*=description]",
        ".lot-description", ".lotDetails", ".description",
        ".lot-details", ".item-description"
    ]
    candidates = []
    for css in selectors:
        for el in soup.select(css):
            t = el.get_text("\n", strip=True)  # keep soft line breaks
            t = re.sub(r"[ \t]*\n[ \t]*", "\n", t).strip()
            if t and len(t) > 20:
                candidates.append(t)

    if not candidates:
        # JSON-LD
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            for b in (data if isinstance(data, list) else [data]):
                if isinstance(b, dict) and isinstance(b.get("description"), str):
                    candidates.append(html.unescape(b["description"]).strip())

    if not candidates:
        ogd = soup.find("meta", property="og:description")
        if ogd and ogd.get("content"):
            candidates.append(ogd["content"].strip())

    if candidates:
        # prefer the longest unique block
        candidates = sorted(set(candidates), key=len, reverse=True)
        # Normalize multi-line to a single block with spaces (UI can render as paragraph)
        best = candidates[0].replace("\r", "").strip()
        best = re.sub(r"[ \t]*\n[ \t]*", " ", best)
        best = re.sub(r"\s{2,}", " ", best).strip()
        return best

    return None

def _extract_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src")
        if not src:
            continue
        cls = " ".join(img.get("class", [])).lower()
        alt = (img.get("alt") or "").lower()
        if any(k in cls for k in ["lot", "gallery", "thumb"]) or "lot" in alt:
            urls.append(urljoin(base_url, src))
        if img.get("srcset"):
            picks = [p.strip().split(" ")[0] for p in img["srcset"].split(",") if p.strip()]
            if picks:
                urls.append(urljoin(base_url, picks[-1]))
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for b in (data if isinstance(b, list) else [data]):
            img = b.get("image")
            if isinstance(img, str):
                urls.append(urljoin(base_url, img))
            elif isinstance(img, list):
                urls.extend(urljoin(base_url, u) for u in img if isinstance(u, str))
    for tag in soup.find_all("meta", property="og:image"):
        if tag.get("content"):
            urls.append(urljoin(base_url, tag["content"]))
    seen, uniq = set(), []
    for u in urls:
        if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", u, re.I) and u not in seen:
            uniq.append(u); seen.add(u)
    return uniq

def _extract_bid_from_labels(soup: BeautifulSoup) -> int | None:
    """
    Robust bid extraction: check multiple labels and nearby nodes.
    """
    LABELS = [
        "Current Bid", "High Bid", "Winning Bid", "Max Bid",
        "Price Realized", "Hammer", "Bid Price", "Current Price", "Price"
    ]

    # 1) Look for common class patterns first (fast path)
    class_patterns = [
        lambda s: s.select_one(".currentbid, .currentBid, .current-bid, [class*=current][class*=bid]"),
        lambda s: s.select_one("[id*=current][id*=bid]"),
        lambda s: s.find(attrs={"data-current-bid": True}),
    ]
    for fn in class_patterns:
        el = fn(soup)
        if el:
            cents = _parse_money_to_cents(_text(el))
            if cents:
                return cents

    # 2) Label-based search
    for lab in LABELS:
        # Find any string containing the label
        for node in soup.find_all(string=lambda s: isinstance(s, str) and lab.lower() in s.lower()):
            # Directly in the string
            cents = _parse_money_to_cents(node)
            if cents:
                return cents
            # Parent container
            parent = getattr(node, "parent", None)
            if parent:
                cents = _parse_money_to_cents(_text(parent))
                if cents:
                    return cents
                # Next siblings nearby
                sib = parent.find_next_sibling()
                if sib:
                    cents = _parse_money_to_cents(_text(sib))
                    if cents:
                        return cents
                # Search a little deeper around parent
                for sub in parent.find_all(True, limit=4):
                    cents = _parse_money_to_cents(_text(sub))
                    if cents:
                        return cents
    return None

def parse_lot(html_text: str):
    soup = _clean_soup(html_text)

    title = _extract_title(soup)
    description = _extract_description(soup)
    base = _base_url_from_soup(soup) or ""
    image_urls = _extract_images(soup, base)

    current_bid_cents = _extract_bid_from_labels(soup)

    bp_pct = None
    for el in soup.find_all(string=lambda s: isinstance(s, str) and "premium" in s.lower()):
        p = _parse_percent(el) or _parse_percent(_text(getattr(el, "parent", None)) if getattr(el, "parent", None) else None)
        if p is not None: bp_pct = p; break

    # We do NOT scrape tax automatically; leave None (use your settings/catalog override)
    tax_pct = None

    end_time_text = None
    for el in soup.find_all(True):
        txt = _text(el)
        if not txt or len(txt) > 200:
            continue
        if re.search(r"(end|close|closing)", txt, re.I) and \
           re.search(r"(\d{1,2}:\d{2}\s*(am|pm)|\d{4}-\d{2}-\d{2})", txt, re.I):
            end_time_text = txt
            break

    return {
        "title": title,
        "image_urls": image_urls,
        "current_bid_cents": current_bid_cents,
        "bp_pct": bp_pct,
        "tax_pct": tax_pct,
        "end_time_text": end_time_text,
        "description": description,
    }
    
def parse_catalog(html_text: str):
    """
    Parse a SINGLE catalog page's HTML and return:
      (title, end_time_text, lot_map_for_this_page)

    - title: str | None
    - end_time_text: str | None  (best-effort)
    - lot_map_for_this_page: dict { lot_number(str) -> absolute lot URL }
    """
    soup = _clean_soup(html_text)

    # Title
    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    if not title:
        h = soup.find(["h1", "h2"])
        if h and h.get_text(strip=True):
            title = h.get_text(" ", strip=True)

    # End time (best-effort scan for closing text + a time or date)
    end_time_text = None
    for el in soup.find_all(True):
        txt = _text(el)
        if not txt or len(txt) > 200:
            continue
        if re.search(r"(end|close|closing|bidding ends|lots start closing)", txt, re.I) and \
           re.search(r"(\d{1,2}:\d{2}\s*(am|pm)|\d{4}-\d{2}-\d{2}|\bET\b|\bEST\b|\bEDT\b)", txt, re.I):
            end_time_text = txt
            break

    # Lots present on THIS page only
    base_url = _base_url_from_soup(soup)
    if not base_url:
        # fall back to origin from any absolute link
        for a in soup.find_all("a", href=True):
            try:
                u = urlparse(a["href"])
                if u.scheme and u.netloc:
                    base_url = f"{u.scheme}://{u.netloc}"; break
            except Exception:
                pass

    lot_map = {}
    for lot_no, lot_href in _extract_catalog_lot_links(soup, base_url or ""):
        lot_map.setdefault(str(lot_no), lot_href)

    return title, end_time_text, lot_map

