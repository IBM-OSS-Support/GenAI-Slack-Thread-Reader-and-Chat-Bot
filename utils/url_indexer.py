# utils/url_indexer.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

def fetch_page_text(url):
    """
    Returns (text, soup) or ("", None) if the page is non-HTML / login-gated / error.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        html = resp.text
        # quick login-gate check
        if "log in" in html.lower() or "ibmid" in html.lower():
            print(f"[WARN] Login required for {url}")
            return "", None

        soup = BeautifulSoup(html, "html.parser")
        # strip out scripts/styles
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        print(f"[DEBUG] Fetched {len(text)} chars of text from {url}")
        return text, soup

    except Exception as e:
        print(f"[ERROR] fetch_page_text({url}): {e}")
        return "", None

def extract_links(soup, base_url):
    """
    Return same-domain hyperlinks found on the root page only.
    """
    base = urlparse(base_url).netloc
    links = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if urlparse(href).netloc == base:
            links.add(href)
    return list(links)

def crawl_and_index(root_url, vs):
    """
    Crawl `root_url` + one level of its same-domain links, then
    chunk & add to the provided FaissVectorStore.
    Returns total number of chunks indexed.
    """
    visited = set()
    to_visit = [root_url]
    all_docs = []
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

    while to_visit:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)

        text, soup = fetch_page_text(url)
        if not text or soup is None:
            continue

        # split
        chunks = splitter.split_text(text)
        docs = [
            Document(page_content=chunk, metadata={"source_url": url, "chunk_index": i})
            for i, chunk in enumerate(chunks)
        ]
        print(f"[DEBUG] {url} → {len(chunks)} chunks")
        all_docs.extend(docs)

        # on root only, grab same-domain links
        if url == root_url:
            links = extract_links(soup, root_url)
            print(f"[DEBUG] {len(links)} same-domain links found")
            to_visit.extend(links)

    if all_docs:
        vs.add_documents(all_docs)
    return len(all_docs)
