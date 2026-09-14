#!/usr/bin/env python3
"""
Scrapes current dogs for adoption from ncar.org.uk and saves to _data/dogs.json.
Run via GitHub Actions on a daily schedule, or manually.

Debug a single dog URL:
  python fetch_dogs.py --debug-url https://ncar.org.uk/animals/some-dog/
"""
import json
import os
import sys
import time

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://ncar.org.uk"
ARCHIVE_URL = f"{BASE_URL}/animal_categories/dogs/"
REQUEST_HEADERS = {
    "User-Agent": "ncar-information-dog-fetcher/1.0",
    "Accept": "text/html",
    "Accept-Language": "en-GB,en;q=0.9",
}
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "_data", "dogs.json")

def build_session():
    session = requests.Session()
    session.trust_env = False
    session.headers.update(REQUEST_HEADERS)
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[403, 429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_soup(url):
    session = build_session()
    candidates = [url]
    if url.startswith("https://ncar.org.uk"):
        candidates.append(url.replace("https://ncar.org.uk", "https://www.ncar.org.uk", 1))

    last_error = None
    for candidate in candidates:
        try:
            response = session.get(candidate, timeout=30, allow_redirects=True)
            if response.status_code == 200:
                return BeautifulSoup(response.text, "html.parser")
            if response.status_code == 404:
                raise requests.HTTPError(response=response)
            last_error = requests.HTTPError(f"{candidate} returned {response.status_code}", response=response)
        except requests.RequestException as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")


def get_archive_dogs():
    """Return list of {name, url} dicts from all paginated archive pages."""
    dogs = []
    page = 1

    while True:
        url = ARCHIVE_URL if page == 1 else f"{ARCHIVE_URL}page/{page}/"
        print(f"  Archive page {page}: {url}")

        try:
            soup = get_soup(url)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                break
            raise

        articles = soup.find_all("article")
        if not articles:
            break

        for article in articles:
            link = article.find("a", class_="kt-imageoverlay-link")
            name_el = article.find("h2", class_="image-overlay-title")
            if link and link.get("href") and name_el:
                dogs.append({
                    "name": name_el.get_text(strip=True),
                    "url": link["href"],
                })

        next_link = soup.find("a", class_="next")
        if not next_link:
            break

        page += 1
        time.sleep(0.5)

    return dogs


def parse_dog_page(url):
    """Return detailed dog info from an individual dog page."""
    soup = get_soup(url)

    # Image — first kt-img-overlay on the page
    image = None
    img = soup.find("img", class_="kt-img-overlay")
    if img and img.get("src"):
        image = img["src"]

    # Status — paragraph styled with the palette accent colour class
    status = None
    status_p = soup.find("p", class_="has-palette-color-1-color")
    if status_p:
        text = status_p.get_text(strip=True)
        status = text.title() if text else None

    # Age, Sex, Breed — located by their <strong> label
    age = sex = breed = None
    for p in soup.find_all("p"):
        strong = p.find("strong")
        if not strong:
            continue
        label = strong.get_text(strip=True).rstrip(":")
        value = p.get_text(strip=True)
        value = value[len(strong.get_text(strip=True)):].lstrip(":").strip()
        if label == "Age":
            age = value
        elif label == "Sex":
            sex = value
        elif label == "Breed":
            breed = value

    # Notes — all kb-dynamic-list-item elements (covers positive and negative lists)
    notes = []
    for li in soup.find_all("li", class_="kb-dynamic-list-item"):
        text = li.get_text(strip=True)
        if text:
            notes.append(text)

    # Description — <p> siblings after the data-block div (nested anywhere in the tree)
    description = []
    data_block_div = soup.find(attrs={"data-block": True})
    if data_block_div:
        past_data_block = False
        for sibling in data_block_div.parent.children:
            if not hasattr(sibling, "name") or sibling.name is None:
                continue
            if sibling is data_block_div:
                past_data_block = True
                continue
            if not past_data_block or sibling.name != "p":
                continue
            # The outer <p> may wrap nested <p> elements
            nested = sibling.find_all("p")
            if nested:
                for p in nested:
                    text = p.get_text(strip=True)
                    if text:
                        description.append(text)
            else:
                text = sibling.get_text(strip=True)
                if text:
                    description.append(text)

    return {
        "image": image,
        "status": status,
        "age": age,
        "sex": sex,
        "breed": breed,
        "notes": notes,
        "description": description,
    }


def debug_dog_page(url):
    """Print a structural dump and parsed result for a single dog page."""
    print(f"\nFetching: {url}\n")
    soup = get_soup(url)

    data_block_div = soup.find(attrs={"data-block": True})
    if not data_block_div:
        print("ERROR: No element with data-block attribute found.")
    else:
        print(f"data-block element: <{data_block_div.name}> data-block={data_block_div.get('data-block')!r}")
        print("Siblings after data-block:\n")
        past = False
        for i, sibling in enumerate(data_block_div.parent.children):
            if not hasattr(sibling, "name") or sibling.name is None:
                continue
            if sibling is data_block_div:
                past = True
                print(f"  [data-block] <{sibling.name}> - pivot")
                continue
            if not past:
                continue
            classes = " ".join(sibling.get("class", []))
            text_preview = sibling.get_text(strip=True)[:100]
            nested_p = len(sibling.find_all("p")) if hasattr(sibling, "find_all") else 0
            print(f"  <{sibling.name}> class={classes!r} nested_p={nested_p} | {text_preview!r}")
        print()

    print("=== Parsed result ===\n")
    result = parse_dog_page(url)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--debug-url":
        debug_dog_page(sys.argv[2])
        return

    print("Fetching archive pages...")
    dogs = get_archive_dogs()
    print(f"Found {len(dogs)} dogs\n")

    # Fail fast in CI if nothing was scraped — prevents committing an empty dataset
    if len(dogs) == 0:
        print("ERROR: No dogs were found while scraping archive pages.", file=sys.stderr)
        print("This usually indicates the remote site blocked the request or the page structure changed.", file=sys.stderr)
        sys.exit(2)
    had_errors = False

    for i, dog in enumerate(dogs):
        print(f"[{i + 1}/{len(dogs)}] {dog['name']} - {dog['url']}")
        try:
            details = parse_dog_page(dog["url"])
            dog.update(details)
        except Exception as e:
            print(f"  Error fetching dog page: {e}", file=sys.stderr)
            had_errors = True
            dog.update({
                "image": None,
                "status": None,
                "age": None,
                "sex": None,
                "breed": None,
                "notes": [],
                "description": [],
            })
        time.sleep(0.5)

    if had_errors:
        print("ERROR: One or more dog pages failed to fetch/parse. _data/dogs.json will not be overwritten.", file=sys.stderr)
        sys.exit(3)

    output = os.path.normpath(OUTPUT_PATH)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(dogs, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(dogs)} dogs to {output}")


if __name__ == "__main__":
    main()
