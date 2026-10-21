#!/usr/bin/env python3
"""Determine the languages used across all own repositories and update the README badges.

Every repository counts equally, regardless of its size: a language counts for a
repository if it makes up at least TECHSTACK_MIN_SHARE percent of that repository
(aliases such as Jupyter Notebook -> Python are merged first, then ignored languages
and frameworks that GitHub reports as languages, such as Vue, are dropped). A badge is
shown for every language that counts in at least TECHSTACK_MIN_REPOS repositories,
ordered by weight. The badge shows the language's weight: every repository has a
total weight of 1, split by its language shares, so all languages add up to 100%.
All remaining languages are summarized in an "Other" badge. The percentages are
rounded with the largest remainder method, so the badges always add up to exactly 100%.

Only repository metadata (the languages endpoint) is read, never repository contents.
Workflow logs of public repositories are public, so this script never prints
repository names or per-repository details - only counts and the aggregated result.

Environment variables:
  GH_TOKEN                 Token with "Metadata: read" access to the repositories (required)
  GITHUB_REPOSITORY_OWNER  Account whose repositories are analyzed (required)
  TECHSTACK_MIN_SHARE      Minimum share (in percent) of a repository for a language to count (default: 20)
  TECHSTACK_MIN_REPOS      Minimum number of repositories for a language to appear (default: 3)
  TECHSTACK_IGNORE         Comma-separated languages that are never counted (default: none)
  TECHSTACK_SOURCE         "installation" (GitHub App token, default) or "user" (local testing with a user token)
  README_PATH              Path to the README (default: README.md)
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import NoReturn

API_URL = "https://api.github.com"
START_MARKER = "<!-- TECHSTACK:AUTO:START -->"
END_MARKER = "<!-- TECHSTACK:AUTO:END -->"
SAFE_LANGUAGE = re.compile(r"^[A-Za-z0-9 +#.'*-]{1,40}$")
BADGE_ALT = re.compile(r"!\[([^\]]*)\]\(")

# Languages that are counted as another language.
LANGUAGE_ALIASES = {"Jupyter Notebook": "Python"}

# Frameworks that GitHub reports as languages. They are never counted as a language
# (their share goes to the other languages of the repository) and are listed manually
# under "Frameworks & Tools" instead.
NON_LANGUAGES = {"Vue"}

# Badge that sums up all languages below the thresholds.
OTHER_LABEL = "Other"

# language -> (badge color, simple-icons slug or None, logo color)
BADGE_STYLES = {
    "C": ("A8B9CC", "c", "black"),
    "C++": ("00599C", "cplusplus", "white"),
    "CSS": ("663399", "css", "white"),
    "Dart": ("0175C2", "dart", "white"),
    "Dockerfile": ("2496ED", "docker", "white"),
    "Go": ("00ADD8", "go", "white"),
    "HTML": ("E34F26", "html5", "white"),
    "Java": ("ED8B00", "openjdk", "white"),
    "JavaScript": ("444C56", "javascript", "F7DF1E"),
    "Jupyter Notebook": ("F37626", "jupyter", "white"),
    "Kotlin": ("7F52FF", "kotlin", "white"),
    "Less": ("2B4F87", "less", "white"),
    "Lua": ("3D3FA0", "lua", "white"),
    "PHP": ("777BB4", "php", "white"),
    "Python": ("3776AB", "python", "white"),
    "R": ("276DC3", "r", "white"),
    "Ruby": ("CC342D", "ruby", "white"),
    "Rust": ("444C56", "rust", "white"),
    "SCSS": ("CC6699", "sass", "white"),
    "Shell": ("4EAA25", "gnubash", "white"),
    "Svelte": ("FF3E00", "svelte", "white"),
    "Swift": ("F05138", "swift", "white"),
    "TeX": ("008080", "latex", "white"),
    "TypeScript": ("3178C6", "typescript", "white"),
}


def fail(message) -> NoReturn:
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def api_get(url, token):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "profile-readme-techstack",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
            next_url = None
            for part in (response.headers.get("Link") or "").split(","):
                if 'rel="next"' in part:
                    next_url = part[part.index("<") + 1 : part.index(">")]
                    # The token is sent along, so only follow links to the GitHub API.
                    if not next_url.startswith(f"{API_URL}/"):
                        fail("Unexpected pagination URL from the GitHub API.")
            return body, next_url
    except urllib.error.HTTPError as error:
        # The URL path may contain a private repository name, so it is not logged.
        fail(f"GitHub API request failed with HTTP {error.code}.")
    except urllib.error.URLError as error:
        fail(f"GitHub API is not reachable: {error.reason}")


def list_repositories(token, owner, source):
    if source == "installation":
        url = f"{API_URL}/installation/repositories?per_page=100"
    elif source == "user":
        url = f"{API_URL}/user/repos?affiliation=owner&per_page=100"
    else:
        fail('TECHSTACK_SOURCE must be "installation" or "user".')

    repositories = []
    while url:
        body, url = api_get(url, token)
        items = body["repositories"] if isinstance(body, dict) else body
        repositories.extend(items)

    return [
        repo["full_name"]
        for repo in repositories
        if repo["owner"]["login"].lower() == owner.lower() and repo["owner"]["type"] == "User"
    ]


def count_languages(token, repositories, ignored, min_share):
    """Return ({language: (repository count, summed per-repository share)}, counted repositories)."""
    stats = {}
    counted = 0
    for full_name in repositories:
        owner, name = full_name.split("/", 1)
        url = f"{API_URL}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/languages"
        languages, _ = api_get(url, token)

        merged = {}
        for language, size in languages.items():
            language = LANGUAGE_ALIASES.get(language, language)
            if normalize(language) not in ignored:
                merged[language] = merged.get(language, 0) + size

        repo_size = sum(merged.values())
        if repo_size == 0:
            continue
        counted += 1
        for language, size in merged.items():
            share = size / repo_size
            count, weight = stats.get(language, (0, 0.0))
            stats[language] = (count + (100 * share >= min_share), weight + share)
    return stats, counted


def shields_escape(text):
    return urllib.parse.quote(text.replace("-", "--").replace("_", "__"), safe="")


def make_badge(label, percent):
    color, logo, logo_color = BADGE_STYLES.get(label, ("555555", None, "white"))
    url = (
        f"https://img.shields.io/badge/{shields_escape(label)}-{shields_escape(f'{percent}%')}-{color}"
        "?style=flat-square"
    )
    if logo:
        url += f"&logo={logo}&logoColor={logo_color}"
    return f"![{label} {percent}%]({url})"


def normalize(name):
    return re.sub(r"[^a-z0-9+#]", "", name.lower().removesuffix(".js"))


def to_percentages(weights, minimums):
    """Round weights to whole percentages that add up to exactly 100 (largest remainder method).

    minimums[i] is the lowest percentage entry i may get; the difference is taken from the largest entry.
    """
    total = sum(weights)
    exact = [100 * weight / total for weight in weights]
    percentages = [int(value) for value in exact]
    by_remainder = sorted(range(len(exact)), key=lambda i: exact[i] - percentages[i], reverse=True)
    for i in by_remainder[: 100 - sum(percentages)]:
        percentages[i] += 1
    for i, minimum in enumerate(minimums):
        while percentages[i] < minimum:
            percentages[i] += 1
            percentages[max(range(len(percentages)), key=percentages.__getitem__)] -= 1
    return percentages


def main():
    token = os.environ.get("GH_TOKEN")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    readme_path = os.environ.get("README_PATH", "README.md")
    source = os.environ.get("TECHSTACK_SOURCE", "installation")
    ignored = {normalize(x) for x in os.environ.get("TECHSTACK_IGNORE", "").split(",") if x.strip()}
    ignored |= {normalize(x) for x in NON_LANGUAGES}
    try:
        min_share = float(os.environ.get("TECHSTACK_MIN_SHARE", "20"))
        min_repos = int(os.environ.get("TECHSTACK_MIN_REPOS", "3"))
    except ValueError:
        fail("TECHSTACK_MIN_SHARE and TECHSTACK_MIN_REPOS must be numbers.")

    if not token:
        fail("GH_TOKEN is not set.")
    if not owner:
        fail("GITHUB_REPOSITORY_OWNER is not set.")

    with open(readme_path, encoding="utf-8") as file:
        readme = file.read()
    if readme.count(START_MARKER) != 1 or readme.count(END_MARKER) != 1:
        fail(f"README must contain exactly one {START_MARKER} and one {END_MARKER}.")
    start = readme.index(START_MARKER) + len(START_MARKER)
    end = readme.index(END_MARKER)
    if start > end:
        fail("TECHSTACK markers are in the wrong order.")

    repositories = list_repositories(token, owner, source)
    if not repositories:
        fail("No repositories found. Is the GitHub App installed on the account?")

    stats, counted = count_languages(token, repositories, ignored, min_share)
    if not stats:
        fail("No language data found.")

    # Languages that already have a manually maintained badge are not duplicated.
    outside_block = readme[: start - len(START_MARKER)] + readme[end:]
    manual = {normalize(alt) for alt in BADGE_ALT.findall(outside_block)}

    # Each repository has a total weight of 1, so the weights of all languages add up to 100%.
    total_weight = sum(weight for _, weight in stats.values())

    candidates = []
    for language, (count, weight) in sorted(stats.items(), key=lambda item: item[1][1], reverse=True):
        if count < min_repos or normalize(language) in manual:
            continue
        if not SAFE_LANGUAGE.match(language):
            print("Skipping a language with unexpected characters.")
            continue
        candidates.append((language, count, weight))

    # Languages below the thresholds are summarized as "Other", so the badges add up to 100%.
    other_weight = max(0.0, total_weight - sum(weight for _, _, weight in candidates))
    percentages = to_percentages(
        [weight for _, _, weight in candidates] + [other_weight],
        [1] * len(candidates) + [0],
    )
    selected = [(language, count, percent) for (language, count, _), percent in zip(candidates, percentages)]
    other_percent = percentages[-1]

    badges = [make_badge(language, percent) for language, _, percent in selected]
    if selected and other_percent > 0:
        badges.append(make_badge(OTHER_LABEL, other_percent))
    badges = "\n".join(badges)
    caption = (
        f"<sub>Automatically calculated from {counted} repositories (public &amp; private)"
        " · each repository weighted equally</sub>"
    )
    block = f"\n{caption}\n\n{badges}\n" if badges else f"\n{caption}\n"
    updated = readme[:start] + block + readme[end:]

    summary = ", ".join(f"{language} {percent}% ({count} repos)" for language, count, percent in selected) or "none"
    if selected and other_percent > 0:
        summary += f", {OTHER_LABEL} {other_percent}%"
    print(f"Analyzed {len(repositories)} repositories.")
    print(f"Languages with at least {min_share:g}% share in at least {min_repos} repos: {summary}")

    if updated != readme:
        with open(readme_path, "w", encoding="utf-8") as file:
            file.write(updated)
        print("README techstack updated.")
    else:
        print("README techstack unchanged.")


if __name__ == "__main__":
    main()
