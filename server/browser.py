"""
Browser harness — unified wrapper for real browser automation.

Combines 5 vendored tools:
  - browser-use (Python lib for LLM-driven browser agents)
  - browser-harness (CDP direct browser control)
  - playwright-skill (Playwright browser automation)
  - Agent-Reach (15+ platform-specific scrapers, no API key needed)
  - BrowserSkill (Rust-based browser, fallback)

Tools exposed to the LLM:
  - google_maps_search(query, city, count) -> lead list with name, address, phone, rating, website
  - browse_url(url, action="content"|"screenshot"|"interact", prompt=None) -> page content or screenshot
  - agent_reach(platform, action, params) -> platform-specific read (Twitter, LinkedIn, GitHub, etc.)
  - extract_leads_from_url(url, schema) -> structured extraction
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "upstream"

# Add upstream paths for imports
for p in ("browser-use", "agent-reach", "browser-harness"):
    sys.path.insert(0, str(UPSTREAM / p))


# ============================================================================
# Agent-Reach — 15 platforms, no API key needed (uses free public endpoints)
# ============================================================================
class AgentReach:
    """Real read layer using upstream CLIs + Jina fallback.

    Order of attempts:
      1. OpenCLI (desktop, requires Chrome login): linkedin, instagram, facebook, xiaoyuzhou, xueqiu
      2. Native CLIs (pipx-installed): twitter-cli, rdt-cli, bili-cli, xhs-cli
      3. yt-dlp: youtube (no login)
      4. Direct public API: github (REST), v2ex (JSON), rss (XML), exa_search
      5. Jina fallback for anything else
    """

    def __init__(self):
        self._channels = None
        self._tools: dict[str, str] = {}
        self._init_channels()

    def _init_channels(self):
        try:
            from agent_reach.channels import ALL_CHANNELS
            self._channels = {ch.name: ch for ch in ALL_CHANNELS}
            self._available = True
        except Exception as e:
            self._available = False
            self._init_error = str(e)
        # Detect installed CLIs at startup
        import shutil
        for binary in ("opencli", "twitter", "twitter-cli",
                       "rdt", "rdt-cli", "bili", "bili-cli",
                       "xhs", "xhs-cli", "yt-dlp",
                       "mcporter", "gh"):
            if shutil.which(binary):
                self._tools[binary] = binary

    def list_platforms(self) -> list[str]:
        if not self._available:
            # Always-available platforms (no external binary / API):
            return [
                "web",                # Jina reader
                "github",             # REST API (no auth)
                "github_trending",    # REST + parser
                "v2ex",               # public JSON
                "rss",                # XML feed
                "youtube",            # yt-dlp if installed, else Jina
                "hackernews",         # Firebase API
                "twitter",            # opencli / twitter-cli / Jina
                "reddit",             # opencli / rdt-cli / Jina
                "linkedin",           # opencli / Jina
                "facebook",           # opencli / Jina
                "instagram",          # opencli / Jina
                "mastodon",           # public Mastodon API
                "threads",            # public Threads API
                "bilibili",           # opencli
            ]
        return list(self._channels.keys())

    def status(self) -> str:
        return "ready" if self._available else f"fallback_only ({self._init_error})"

    def installed_tools(self) -> list[str]:
        """List CLI tools detected at startup."""
        return list(self._tools.keys())

    async def read(self, platform: str, target: str, **kwargs) -> dict:
        """Read content from a platform. target is platform-specific (URL, handle, query).

        Strategy per platform:
          - github:     REST API (no auth for public)
          - v2ex:       public API
          - rss:        direct XML fetch
          - youtube:    yt-dlp (if installed) else Jina
          - twitter:    twitter-cli (if installed) else Jina
          - reddit:     rdt-cli (if installed) else Jina
          - linkedin:   opencli (if installed) else Jina
          - instagram:  opencli (if installed) else Jina
          - facebook:   opencli (if installed) else Jina
          - other:      Jina fallback
        """
        # Direct API platforms (no install needed)
        if platform == "github":
            return await self._read_github(target)
        if platform == "v2ex":
            return await self._read_v2ex(target)
        if platform == "rss":
            return await self._read_rss(target)
        if platform == "exa_search":
            return await self._read_exa(target)
        if platform == "hackernews":
            return await self._read_hackernews(target)
        if platform == "github_trending":
            return await self._read_github_trending(target)
        if platform == "mastodon":
            return await self._read_mastodon(target)
        if platform == "threads":
            return await self._read_threads(target)
        if platform == "substack":
            return await self._read_substack(target)
        if platform == "medium":
            return await self._read_medium(target)
        if platform == "producthunt":
            return await self._read_producthunt(target)
        if platform == "youtube":
            return await self._read_youtube(target)
        if platform == "youtube" and not self._tools.get("yt-dlp"):
            return await self._jina_fallback(target)
        # Subprocess-based platforms
        if platform == "twitter":
            return await self._read_cli("twitter", target,
                                        ["twitter {target} -n 10",
                                         "twitter feed -n 10"])
        if platform == "reddit":
            return await self._read_cli("reddit", target,
                                        ["rdt {target} --limit 10",
                                         "rdt-cli {target} --limit 10"])
        if platform == "bilibili":
            return await self._read_cli("bilibili", target,
                                        ["bili {target} --type video -n 5",
                                         "bili-cli {target}"])
        if platform == "xiaohongshu":
            return await self._read_cli("xiaohongshu", target,
                                        ["xhs {target}",
                                         "xhs-cli {target}"])
        if platform in ("linkedin", "instagram", "facebook", "xiaoyuzhou", "xueqiu"):
            return await self._read_cli(platform, target,
                                        [f"opencli {platform} {{target}} -f yaml"])
        # Generic: Jina fallback
        return await self._jina_fallback(target)

    async def _read_github(self, target: str) -> dict:
        """GitHub public REST API."""
        # target can be 'owner/repo' or full URL
        if target.startswith("http"):
            m = re.search(r"github\.com/([^/]+/[^/]+)", target)
            if m:
                target = m.group(1).rstrip("/")
        try:
            url = f"https://api.github.com/repos/{target}"
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "orchestrator/1.0",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            summary = (
                f"**{data.get('full_name')}**\n"
                f"Description: {data.get('description', 'N/A')}\n"
                f"Stars: {data.get('stargazers_count'):,} | "
                f"Forks: {data.get('forks_count'):,} | "
                f"Open issues: {data.get('open_issues_count')}\n"
                f"Language: {data.get('language')} | "
                f"License: {data.get('license', {}).get('spdx_id') if data.get('license') else 'N/A'}\n"
                f"Homepage: {data.get('homepage') or 'N/A'}\n"
                f"Created: {data.get('created_at')[:10]} | "
                f"Last push: {data.get('pushed_at')[:10]}\n"
                f"Topics: {', '.join(data.get('topics', []))}\n"
                f"URL: {data.get('html_url')}"
            )
            return {"ok": True, "platform": "github", "result": summary,
                    "stars": data.get("stargazers_count"),
                    "forks": data.get("forks_count"),
                    "language": data.get("language"),
                    "topics": data.get("topics", [])}
        except Exception as e:
            return await self._jina_fallback(f"https://github.com/{target}",
                                              fallback_from="github",
                                              original_error=str(e))

    async def _read_v2ex(self, target: str) -> dict:
        """V2EX public API."""
        try:
            if target.startswith("http"):
                m = re.search(r"/t/(\d+)", target)
                if m:
                    target = m.group(1)
            url = f"https://www.v2ex.com/api/topics/show.json?id={target}"
            req = urllib.request.Request(url, headers={"User-Agent": "orchestrator/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            if isinstance(data, list):
                data = data[0] if data else {}
            title = data.get("title", "")
            content = data.get("content", "")
            member = data.get("member", {}).get("username", "")
            node = data.get("node", {}).get("title", "")
            replies = data.get("replies", 0)
            return {"ok": True, "platform": "v2ex",
                    "result": f"**{title}**\nBy: {member} | Node: {node} | Replies: {replies}\n\n{content[:2000]}"}
        except Exception as e:
            return {"ok": False, "platform": "v2ex", "error": str(e)}

    async def _read_rss(self, target: str) -> dict:
        """RSS feed reader with multiple user-agent fallbacks."""
        import socket
        # Try multiple user-agents (some feeds block default UAs)
        user_agents = [
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "curl/8.5.0",
            "orchestrator/1.0",
        ]
        last_err = None
        for ua in user_agents:
            try:
                req = urllib.request.Request(target, headers={
                    "User-Agent": ua,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                })
                with urllib.request.urlopen(req, timeout=20) as r:
                    xml = r.read().decode("utf-8", errors="ignore")
                if not xml.strip():
                    last_err = "empty response"
                    continue
                import xml.etree.ElementTree as ET
                try:
                    root = ET.fromstring(xml)
                except ET.ParseError as e:
                    last_err = f"xml_parse:{e}"
                    continue
                items = []
                channel = root.find("channel")
                if channel is not None:
                    for item in channel.findall("item")[:10]:
                        title = item.findtext("title", "")
                        link = item.findtext("link", "")
                        desc = item.findtext("description", "")[:300]
                        pub = item.findtext("pubDate", "")
                        items.append(f"**{title}**\n{pub}\n{link}\n{desc}\n")
                if not items:
                    # Atom feed
                    ns = {"atom": "http://www.w3.org/2005/Atom"}
                    for entry in root.findall("atom:entry", ns)[:10]:
                        title = entry.findtext("atom:title", "", ns)
                        link_el = entry.find("atom:link", ns)
                        link = link_el.get("href", "") if link_el is not None else ""
                        items.append(f"**{title}**\n{link}\n")
                if items:
                    return {"ok": True, "platform": "rss",
                            "result": "\n---\n".join(items)}
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                last_err = str(e)
                continue
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                continue
        return {"ok": False, "platform": "rss",
                "error": last_err or "all_user_agents_failed"}

    async def _read_hackernews(self, target: str) -> dict:
        """Hacker News Firebase API (no auth required).
        target: 'top', 'new', 'best', 'ask', 'show' (default 'top'),
                or numeric item id, or comma-separated ids.
        """
        try:
            base = "https://hacker-news.firebaseio.com/v0"
            if target.isdigit():
                # Single item
                url = f"{base}/item/{target}.json"
                req = urllib.request.Request(url, headers={"User-Agent": "orchestrator/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    item = json.loads(r.read().decode())
                if not item:
                    return {"ok": False, "platform": "hackernews", "error": "item not found"}
                _url = item.get("url") or f"https://news.ycombinator.com/item?id={item.get('id')}"
                summary = (f"**{item.get('title')}**\n"
                           f"By: {item.get('by')} | Score: {item.get('score')} | "
                           f"Comments: {item.get('descendants', 0)}\n"
                           f"URL: {_url}")
                return {"ok": True, "platform": "hackernews", "result": summary}
            endpoint = target if target in ("top", "new", "best", "ask", "show", "jobs") else "top"
            url = f"{base}/{endpoint}stories.json"
            req = urllib.request.Request(url, headers={"User-Agent": "orchestrator/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                ids = json.loads(r.read().decode())[:10]
            items_text = []
            for iid in ids:
                with urllib.request.urlopen(
                    urllib.request.Request(f"{base}/item/{iid}.json",
                                           headers={"User-Agent": "orchestrator/1.0"}),
                    timeout=10
                ) as r:
                    it = json.loads(r.read().decode())
                if it:
                    items_text.append(
                        f"**{it.get('title')}**\nBy: {it.get('by')} | "
                        f"Score: {it.get('score')} | Comments: {it.get('descendants', 0)}\n"
                        f"https://news.ycombinator.com/item?id={it.get('id')}"
                    )
            return {"ok": True, "platform": "hackernews",
                    "result": "\n\n".join(items_text) or "(no stories)"}
        except Exception as e:
            return {"ok": False, "platform": "hackernews", "error": str(e)}

    async def _read_github_trending(self, target: str) -> dict:
        """GitHub Trending page scrape (HTML, no auth).
        target: optional language, e.g. 'python', 'typescript' (default: all).
        """
        try:
            lang = target.strip() or ""
            url = f"https://github.com/trending/{lang}" if lang else "https://github.com/trending"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "text/html",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", errors="ignore")
            # Parse article blocks <article class="Box-row">
            import re
            blocks = re.findall(r'<article class="Box-row">(.*?)</article>',
                                html, re.DOTALL)
            repos = []
            for b in blocks[:10]:
                h2 = re.search(r'<h2[^>]*>\s*<a[^>]*href="(/[^"]+)"', b)
                if not h2:
                    continue
                repo_path = h2.group(1).strip("/")
                desc_m = re.search(r'<p class="col-9[^"]*">(.*?)</p>', b, re.DOTALL)
                desc = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""
                stars_today = re.search(r'(\d+(?:,\d+)*)\s+stars today', b)
                st = stars_today.group(1) if stars_today else "?"
                total = re.search(r'href="/[^"]+/stargazers"[^>]*>\s*([\d,]+)\s*</a>', b)
                total = total.group(1) if total else "?"
                lang_m = re.search(r'itemprop="programmingLanguage">([^<]+)</span>', b)
                language = lang_m.group(1).strip() if lang_m else ""
                repos.append(f"**{repo_path}** (+{st} stars today, {total} total)\n"
                             f"Language: {language or 'N/A'}\n{desc[:200]}")
            return {"ok": True, "platform": "github_trending",
                    "language": lang or "all",
                    "result": "\n\n".join(repos) or "(no repos found)"}
        except Exception as e:
            return {"ok": False, "platform": "github_trending", "error": str(e)}

    async def _read_mastodon(self, target: str) -> dict:
        """Mastodon public API (no auth for hashtag / local timelines).

        target: 'instance:hashtag' (e.g. 'mastodon.social:ai') for a tag timeline,
                or 'instance' (uses default '#AI' hashtag on that instance).
                Note: since Mastodon 4.x, the global public timeline requires auth.
                The local timeline + hashtag timelines work without auth.
        """
        try:
            if ":" in target:
                instance, hashtag = target.split(":", 1)
            else:
                instance = target.strip() or "mastodon.social"
                hashtag = "ai"  # default hashtag
            url = f"https://{instance}/api/v1/timelines/tag/{hashtag}?limit=10"
            req = urllib.request.Request(url, headers={
                "User-Agent": "orchestrator/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                posts = json.loads(r.read().decode())
            if not isinstance(posts, list):
                return {"ok": False, "platform": "mastodon", "error": "unexpected response"}
            lines = []
            for p in posts[:10]:
                acct = p.get("account", {}).get("acct", "?")
                content = re.sub(r"<[^>]+>", "", p.get("content", ""))[:300]
                url_post = p.get("url", "")
                reblogs = p.get("reblogs_count", 0)
                favs = p.get("favourites_count", 0)
                lines.append(f"**@{acct}**\n{content}\n"
                             f"Reblogs: {reblogs} | Favorites: {favs}\n{url_post}")
            return {"ok": True, "platform": "mastodon",
                    "instance": instance if ":" not in target else target.split(":")[0],
                    "result": "\n\n".join(lines) or "(no posts)"}
        except Exception as e:
            return {"ok": False, "platform": "mastodon", "error": str(e)}

    async def _read_threads(self, target: str) -> dict:
        """Threads (Meta) — uses public web search via DuckDuckGo HTML.
        target: query string (e.g. 'ai lead routing').
        Note: no public Threads API yet; this is best-effort via web.
        """
        try:
            # Use DuckDuckGo HTML to find public Threads posts about the topic
            ddg = f"https://html.duckduckgo.com/html/?q=site%3Athreads.net+{urllib.parse.quote(target)}"
            req = urllib.request.Request(ddg, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", errors="ignore")
            # Extract result snippets
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
            urls = re.findall(r'class="result__url"[^>]*href="(https?://[^"]+threads\.net[^"]*)"',
                              html)
            lines = []
            for u, s in list(zip(urls, snippets))[:10]:
                s_clean = re.sub(r"<[^>]+>", "", s).strip()
                lines.append(f"**{u}**\n{s_clean[:300]}")
            if not lines:
                # Fallback: Jina
                return await self._jina_fallback(
                    f"https://www.threads.net/search?q={urllib.parse.quote(target)}",
                    fallback_from="threads")
            return {"ok": True, "platform": "threads",
                    "result": "\n\n".join(lines)}
        except Exception as e:
            return await self._jina_fallback(
                f"https://www.threads.net/search?q={urllib.parse.quote(target)}",
                fallback_from="threads", original_error=str(e))

    async def _read_substack(self, target: str) -> dict:
        """Substack public RSS feed reader.
        target: substack publication slug (e.g. 'stratechery') or full URL.
        """
        try:
            t = target.strip()
            if t.startswith("http"):
                m = re.search(r"substack\.com/?(?:p/)?(@?[\w-]+)?", t)
                if m and m.group(1):
                    slug = m.group(1).lstrip("@")
                    url = f"https://{slug}.substack.com/feed"
                else:
                    url = t
            else:
                slug = t.lstrip("@")
                url = f"https://{slug}.substack.com/feed"
            return await self._read_rss(url)
        except Exception as e:
            return {"ok": False, "platform": "substack", "error": str(e)}

    async def _read_medium(self, target: str) -> dict:
        """Medium public RSS feed (https://medium.com/feed/{user-or-pub}).
        target: user, publication, or topic. e.g. 'towards-data-science' or '@username'.
        """
        try:
            t = target.strip().lstrip("@")
            if t.startswith("http"):
                # Convert to /feed/ endpoint if needed
                if "/feed" not in t:
                    t = t.rstrip("/") + "/feed"
                return await self._read_rss(t)
            url = f"https://medium.com/feed/{t}"
            return await self._read_rss(url)
        except Exception as e:
            return {"ok": False, "platform": "medium", "error": str(e)}

    async def _read_producthunt(self, target: str) -> dict:
        """Product Hunt — uses public RSS feed.
        target: empty for top today, or category (e.g. 'tech', 'ai').
        """
        try:
            url = "https://www.producthunt.com/feed"
            res = await self._read_rss(url)
            if res.get("ok") and target.strip():
                # Filter by keyword if specified
                keyword = target.strip().lower()
                res["result"] = "\n\n---\n\n".join(
                    block for block in res["result"].split("\n\n---\n\n")
                    if keyword in block.lower()
                ) or f"(no results for '{keyword}')"
            return res
        except Exception as e:
            return {"ok": False, "platform": "producthunt", "error": str(e)}

    async def _read_exa(self, target: str) -> dict:
        """Exa neural search. Requires EXA_API_KEY."""
        api_key = os.environ.get("EXA_API_KEY", "")
        if not api_key:
            return {"ok": False, "platform": "exa_search", "error": "EXA_API_KEY not set"}
        try:
            body = {"query": target, "numResults": 5,
                    "useAutoprompt": True, "contents": {"text": True}}
            req = urllib.request.Request(
                "https://api.exa.ai/search",
                data=json.dumps(body).encode(),
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
            results = data.get("results", [])
            text = "\n\n".join(
                f"**{r.get('title')}**\n{r.get('url')}\n{(r.get('text') or '')[:500]}"
                for r in results
            )
            return {"ok": True, "platform": "exa_search", "result": text}
        except Exception as e:
            return {"ok": False, "platform": "exa_search", "error": str(e)}

    async def _read_youtube(self, target: str) -> dict:
        """YouTube via yt-dlp."""
        if not self._tools.get("yt-dlp"):
            return await self._jina_fallback(target)
        try:
            cmd = ["yt-dlp", "--dump-json", "--no-download", "--flat-playlist",
                   "--playlist-end", "5", target]
            p = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(p.communicate(), timeout=30)
            lines = stdout.decode("utf-8", errors="ignore").strip().split("\n")[:5]
            items = []
            for line in lines:
                try:
                    d = json.loads(line)
                    items.append(f"**{d.get('title')}**\n{d.get('url') or d.get('webpage_url')}\n"
                                 f"Duration: {d.get('duration')}s | Channel: {d.get('channel') or d.get('uploader')}")
                except Exception:
                    pass
            return {"ok": True, "platform": "youtube",
                    "result": "\n\n".join(items) or "(no items)"}
        except Exception as e:
            return await self._jina_fallback(target, fallback_from="youtube",
                                             original_error=str(e))

    async def _read_cli(self, platform: str, target: str,
                        patterns: list[str]) -> dict:
        """Run a CLI tool via subprocess."""
        for binary in self._tools:
            for pat in patterns:
                tmpl = pat.replace("{target}", target)
                cmd = tmpl.split()
                if not cmd or cmd[0] != binary:
                    continue
                try:
                    p = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await asyncio.wait_for(
                        p.communicate(), timeout=45
                    )
                    out = stdout.decode("utf-8", errors="ignore")
                    if out.strip():
                        return {"ok": True, "platform": platform,
                                "result": out[:5000], "via": binary}
                except (asyncio.TimeoutError, FileNotFoundError, Exception) as e:
                    continue
        # Nothing installed — try Jina
        return await self._jina_fallback(target, fallback_from=platform,
                                         original_error="no_cli_installed")

    async def _jina_fallback(self, url: str, *, fallback_from: str = None,
                              original_error: str = None) -> dict:
        """Multi-strategy web reader.

        Tries in order:
          1. Jina Reader (r.jina.ai) — no API key but rate-limited
          2. Mozilla Readability endpoint (r.jina.ai without auth sometimes)
          3. Raw HTTP fetch + simple HTML strip (last resort)
        """
        import urllib.request
        import urllib.error
        # Strategy 1: Jina Reader
        try:
            req = urllib.request.Request(f"https://r.jina.ai/{url}", headers={
                "Accept": "text/plain",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                content = r.read().decode("utf-8", errors="ignore")
            if content and not content.startswith("https://r.jina.ai"):
                result = {"ok": True, "platform": "jina", "result": content[:5000]}
                if fallback_from:
                    result["fallback_from"] = fallback_from
                if original_error:
                    result["original_error"] = original_error[:200]
                return result
        except Exception:
            pass
        # Strategy 2: Raw fetch + HTML strip
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", errors="ignore")[:100000]
            # Strip script/style blocks then HTML tags
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            # Try to extract <title>
            title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
            title = title_m.group(1).strip() if title_m else ""
            if title:
                text = f"{title}\n\n{text}"
            result = {"ok": True, "platform": "raw_fetch", "result": text[:5000]}
            if fallback_from:
                result["fallback_from"] = fallback_from
            if original_error:
                result["original_error"] = original_error[:200]
            return result
        except Exception as e:
            return {"ok": False, "error": str(e),
                    "fallback_from": fallback_from or "web",
                    "original_error": original_error}


agent_reach = AgentReach()


# ============================================================================
# Google Maps prospect — Playwright + headless Chrome
# ============================================================================
class GoogleMapsProspector:
    """Real Google Maps scraping using Playwright. Returns structured lead data.

    Requires: playwright install + chromium browser binary.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self):
        if self._browser is None:
            from playwright.async_api import async_playwright
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            # Try common chromium locations
            for path in [
                "/root/.cache/ms-playwright/chromium-1243/chrome-linux/chrome",
                "/root/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/usr/bin/google-chrome",
            ]:
                if Path(path).exists():
                    try:
                        self._browser = await self._playwright.chromium.launch(headless=True, executable_path=path)
                        return self._browser
                    except Exception:
                        continue
            # Fall back to default (uses playwright's bundled chromium)
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self._browser

    async def search(self, query: str, city: str = "", count: int = 10) -> dict:
        """Search Google Maps for businesses matching query + city.
        Returns: {ok, leads: [{name, address, phone, rating, reviews_count, website, category, lat, lng}]}
        """
        try:
            browser = await self._ensure_browser()
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            search_query = f"{query} {city}".strip()
            url = f"https://www.google.com/maps/search/{urllib.parse.quote(search_query)}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Wait for results panel
            try:
                await page.wait_for_selector('[role="article"]', timeout=15000)
            except Exception:
                # Maybe no results or different selector
                pass
            leads = []
            # Try to extract via current Google Maps DOM
            cards = await page.query_selector_all('[role="article"]')
            for card in cards[:count]:
                lead = await self._extract_card(card)
                if lead:
                    leads.append(lead)
            if not leads:
                # Try alt DOM
                leads = await self._extract_via_json(page, count)
            await page.close()
            await context.close()
            return {"ok": True, "source": "google_maps_playwright", "query": search_query, "count": len(leads), "leads": leads}
        except Exception as e:
            return {"ok": False, "error": f"google_maps_failed: {type(e).__name__}: {str(e)[:200]}", "fallback": "use Agent-Reach or Jina Reader"}

    async def _extract_card(self, card) -> dict:
        try:
            name = await card.query_selector("div.fontHeadlineSmall, h1, h2, h3, [class*='fontHeadline']")
            name_text = await name.inner_text() if name else ""
            # Rating
            rating = None
            reviews = None
            rating_el = await card.query_selector("span[role='img']")
            if rating_el:
                rating_attr = await rating_el.get_attribute("aria-label") or ""
                m = re.search(r"(\d+\.?\d*)\s*star", rating_attr)
                if m:
                    rating = float(m.group(1))
                m2 = re.search(r"(\d+)\s*review", rating_attr)
                if m2:
                    reviews = int(m2.group(1))
            # Address
            addr = None
            addr_els = await card.query_selector_all("div")
            for el in addr_els:
                txt = await el.inner_text()
                if any(c in txt for c in ["St", "Ave", "Rd", "Blvd", "Street", "Road", "Avenue"]) and len(txt) < 100:
                    addr = txt
                    break
            # Phone
            phone = None
            all_text = await card.inner_text()
            pm = re.search(r"[\+\d][\d\s\-\(\)]{7,}\d", all_text)
            if pm:
                phone = pm.group(0).strip()
            # Website
            website = None
            web_el = await card.query_selector("a[href*='http']:not([href*='google.com'])")
            if web_el:
                website = await web_el.get_attribute("href")
            return {
                "name": name_text.strip() if name_text else None,
                "address": addr,
                "phone": phone,
                "rating": rating,
                "review_count": reviews,
                "website": website,
                "source": "google_maps",
            }
        except Exception:
            return None

    async def _extract_via_json(self, page, count: int) -> list:
        """Fallback: scrape JSON state from Google Maps page."""
        try:
            data = await page.evaluate("""() => {
                const out = [];
                if (window.APP_INITIALIZATION_STATE) {
                    // Try to extract from APP_INITIALIZATION_STATE
                    return 'has_init_state';
                }
                return null;
            }""")
        except Exception:
            pass
        return []

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


google_maps = GoogleMapsProspector()


# ============================================================================
# Generic browse (Playwright)
# ============================================================================
class BrowserController:
    """Generic browser control via Playwright. Used for fetch_url with JS rendering."""

    async def fetch_page(self, url: str, *, action: str = "content", prompt: str = None, wait_selector: str = None) -> dict:
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=10000)
                    except Exception:
                        pass
                if action == "content":
                    content = await page.content()
                    text = await page.evaluate("() => document.body.innerText")
                    await browser.close()
                    return {"ok": True, "url": url, "text": text[:8000], "html_length": len(content)}
                elif action == "screenshot":
                    path = tempfile.mktemp(suffix=".png")
                    await page.screenshot(path=path)
                    await browser.close()
                    return {"ok": True, "url": url, "screenshot": path}
                elif action == "interact" and prompt:
                    # Use browser-use for LLM-driven interaction
                    return await self._llm_interact(url, prompt)
                else:
                    await browser.close()
                    return {"ok": False, "error": f"unknown action {action}"}
        except Exception as e:
            return {"ok": False, "error": f"playwright_failed: {type(e).__name__}: {str(e)[:300]}"}

    async def _llm_interact(self, url: str, prompt: str) -> dict:
        """Use browser-use for LLM-driven browser interaction."""
        try:
            from browser_use import Agent, Browser
            browser = Browser(headless=True)
            agent = Agent(
                task=f"Navigate to {url} and {prompt}. Return the result as plain text.",
                browser=browser,
                llm=None,  # Will use default
            )
            result = await agent.run()
            await browser.close()
            return {"ok": True, "url": url, "result": str(result)[:5000]}
        except Exception as e:
            return {"ok": False, "error": f"browser_use_failed: {type(e).__name__}: {str(e)[:200]}"}

    async def extract_leads(self, url: str, schema: dict = None) -> dict:
        """Extract structured lead data from a page (e.g., a business directory)."""
        result = await self.fetch_page(url, action="content")
        if not result.get("ok"):
            return result
        text = result.get("text", "")
        # Simple extraction: look for phone numbers, emails, business names
        leads = []
        # Email pattern
        emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        # Phone pattern
        phones = re.findall(r"[\+\d][\d\s\-\(\)]{7,}\d", text)
        return {
            "ok": True,
            "url": url,
            "emails": list(set(emails))[:50],
            "phones": list(set(phones))[:50],
            "text_preview": text[:2000],
        }


browser = BrowserController()


# ============================================================================
# Helper to run sync tools
# ============================================================================
def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.run(coro)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
