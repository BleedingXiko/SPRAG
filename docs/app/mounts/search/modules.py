from sprag import Module, debounce, dom


class SearchModule(Module):
    def __init__(self, screen=None, state=None):
        super().__init__(screen=screen, state=state or {})
        self._docs = []
        self._loaded = False

    def on_start(self):
        initial = self._read_query_param()
        input_el = dom.query("[data-role='search-input']", self.element)
        if input_el and initial:
            input_el.value = initial
        self.delegate(self.element, "input", "[data-role='search-input']", self.on_input)
        self._set_status("Loading search index…")
        self.load_index(initial)

    async def load_index(self, initial_query):
        try:
            response = await browser.fetch("../static/search-index.json")
            payload = await response.json()
            self._docs = self._prepare(payload["docs"])
            self._loaded = True
            self._run(initial_query)
        except Exception as err:
            self._render_results([], [])
            self._set_status("Couldn't load the search index.")

    @debounce(0.12)
    def on_input(self, event, target):
        self._run(target.value)

    def _run(self, query):
        if not self._loaded:
            return None
        trimmed = query.strip()
        if not trimmed:
            self._render_results([], [])
            self._set_status("Type to search the docs.")
            return None
        tokens = self._tokenize(trimmed.lower())
        if len(tokens) == 0:
            self._render_results([], [])
            self._set_status("Type to search the docs.")
            return None
        results = self._score(tokens)
        if len(results) == 0:
            self._render_results([], [])
            self._set_status("No results for “" + trimmed + "”.")
            return None
        self._render_results(results, tokens)
        self._set_status(str(len(results)) + " result" + ("" if len(results) == 1 else "s") + " for “" + trimmed + "”")

    def _set_status(self, text):
        el = dom.query("[data-role='search-status']", self.element)
        if el:
            el.textContent = text

    def _render_results(self, items, tokens):
        container = dom.query("[data-role='search-results']", self.element)
        if not container:
            return None
        dom.clear(container)
        doc = browser.document
        for item in items:
            li = doc.createElement("li")
            li.className = "search-result"
            a = doc.createElement("a")
            a.href = self._with_base(item["url"])
            a.className = "search-result-link"
            section = doc.createElement("div")
            section.className = "search-result-section"
            section.textContent = item["section"]
            a.appendChild(section)
            title = doc.createElement("div")
            title.className = "search-result-title"
            self._highlight(title, item["title"], tokens)
            a.appendChild(title)
            if item["snippet"]:
                snippet = doc.createElement("div")
                snippet.className = "search-result-snippet"
                self._highlight(snippet, item["snippet"], tokens)
                a.appendChild(snippet)
            li.appendChild(a)
            container.appendChild(li)

    def _highlight(self, parent, text, tokens):
        if not text:
            return None
        if len(tokens) == 0:
            parent.textContent = text
            return None
        doc = browser.document
        text_lc = text.lower()
        n = len(text)
        i = 0
        plain_start = 0
        while i < n:
            matched_len = 0
            for token in tokens:
                tlen = len(token)
                if text_lc.slice(i, i + tlen) == token:
                    matched_len = tlen
                    break
            if matched_len > 0:
                if i > plain_start:
                    parent.appendChild(doc.createTextNode(text.slice(plain_start, i)))
                mark = doc.createElement("mark")
                mark.textContent = text.slice(i, i + matched_len)
                parent.appendChild(mark)
                i = i + matched_len
                plain_start = i
            else:
                i = i + 1
        if plain_start < n:
            parent.appendChild(doc.createTextNode(text.slice(plain_start, n)))

    def _tokenize(self, query_lc):
        tokens = []
        for word in query_lc.split(" "):
            cleaned = word.strip()
            if cleaned:
                tokens.push(cleaned)
        return tokens

    def _score(self, tokens):
        matches = []
        for doc in self._docs:
            score = 0
            all_hit = True
            for token in tokens:
                if token in doc["title_lc"]:
                    score += 10
                elif token in doc["headings_lc"]:
                    score += 4
                elif token in doc["description_lc"]:
                    score += 3
                elif token in doc["body_lc"]:
                    score += 1
                else:
                    all_hit = False
                    break
            if all_hit and score > 0:
                matches.push({
                    "title": doc["title"],
                    "url": doc["url"],
                    "section": doc["section"],
                    "snippet": self._snippet(doc, tokens),
                    "score": score,
                })

        matches.sort(lambda a, b: b["score"] - a["score"])
        return matches.slice(0, 30)

    def _snippet(self, doc, tokens):
        body = doc["body"]
        body_lc = doc["body_lc"]
        if not body:
            return doc["description"] or ""

        pos = -1
        for token in tokens:
            i = body_lc.indexOf(token)
            if i >= 0:
                pos = i
                break

        if pos < 0:
            tail = "…" if len(body) > 160 else ""
            return body.slice(0, 160) + tail

        start = pos - 60
        if start < 0:
            start = 0
        end = pos + 120
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(body) else ""
        return prefix + body.slice(start, end) + suffix

    def _prepare(self, docs):
        prepared = []
        for d in docs:
            headings_joined = d["headings"].join(" ")
            prepared.push({
                "title": d["title"],
                "title_lc": d["title"].lower(),
                "url": d["url"],
                "section": d["section"],
                "description": d["description"],
                "description_lc": d["description"].lower(),
                "headings_lc": headings_joined.lower(),
                "body": d["body"],
                "body_lc": d["body"].lower(),
            })
        return prepared

    def _with_base(self, url):
        base = browser.__SPRAG_BASE__ or ""
        if base and url and url.slice(0, 1) == "/":
            return base + url
        return url

    def _read_query_param(self):
        raw = browser.location.search
        if not raw or len(raw) < 3:
            return ""
        body = raw.slice(1)
        for pair in body.split("&"):
            if pair.slice(0, 2) == "q=":
                value = pair.slice(2).replace_all("+", " ")
                return browser.decodeURIComponent(value)
        return ""
